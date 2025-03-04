import sys
import threading
import socket
import socketserver
import json
import time
from functools import partial

from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QUrl, QEventLoop, QTimer, Qt
from PyQt5.QtWebChannel import QWebChannel

import pyttsx3
from pynput import keyboard, mouse

# ------------------------ Global Variables & TTS Setup ------------------------

typed_message = ""            # Global buffer for captured keystrokes
message_lock = threading.Lock()

server_instance = None        # Global reference to the running local server (if any)
client_instance = None        # Global reference to a TCP client connection (if connected)

# Initialize TTS engine (pyttsx3)
tts_engine = pyttsx3.init()
current_voice_id = None       # Selected TTS voice (None = default)

def speak_message(message):
    """Speak the provided message using TTS."""
    if message.strip() == "":
        return
    try:
        if current_voice_id:
            tts_engine.setProperty('voice', current_voice_id)
        tts_engine.say(message)
        tts_engine.runAndWait()
    except Exception as e:
        print("TTS Error:", e)

# ---------------------- Local Server & UDP Announcement -----------------------

BROADCAST_PORT = 54321  # UDP port used for server announcements

class LocalServer(socketserver.ThreadingTCPServer):
    """Local TCP server for message broadcasting and client join handling."""
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, bridge=None):
        super().__init__(server_address, RequestHandlerClass)
        self.clients = []   # List of connected client sockets
        self.bridge = bridge  # Reference to the QWebChannel bridge for GUI updates
        self.broadcasting = True
        # Start UDP broadcast thread for discovery
        self.broadcast_thread = threading.Thread(target=self.broadcast_loop, daemon=True)
        self.broadcast_thread.start()

    def add_client(self, client_socket):
        with threading.Lock():
            self.clients.append(client_socket)
        if self.bridge:
            self.bridge.updateConsole(f"Client connected. Total: {len(self.clients)}")

    def remove_client(self, client_socket):
        with threading.Lock():
            if client_socket in self.clients:
                self.clients.remove(client_socket)
        if self.bridge:
            self.bridge.updateConsole(f"Client disconnected. Total: {len(self.clients)}")

    def broadcast_message(self, message):
        """Broadcast a JSON message to all connected clients."""
        payload = json.dumps({"type": "message", "content": message}).encode('utf-8')
        for client in list(self.clients):
            try:
                client.sendall(payload)
            except Exception as e:
                print("Broadcast error:", e)
                self.remove_client(client)
        # Also update host console and speak the message
        if self.bridge:
            self.bridge.updateConsole(f"Broadcast: {message}")
        speak_message(message)

    def broadcast_loop(self):
        """Continuously broadcast server announcement via UDP."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Allow reuse of the UDP port
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        announcement = json.dumps({
            "type": "server_announcement",
            "port": self.server_address[1],
            "name": "Local Server"
        })
        while self.broadcasting:
            try:
                sock.sendto(announcement.encode('utf-8'), ('<broadcast>', BROADCAST_PORT))
                time.sleep(2)
            except Exception as e:
                print("UDP broadcast error:", e)
                break
        sock.close()

    def shutdown(self):
        self.broadcasting = False
        super().shutdown()

class ClientHandler(socketserver.BaseRequestHandler):
    """
    Handles communication with a connected client.
    Protocol:
      - Client sends join request JSON: {"type": "join", "name": "ClientName"}
      - Server asks the host to accept/reject the join request.
      - If accepted, responds with {"type": "join", "status": "accepted"}; otherwise rejected.
      - Afterwards, messages are exchanged as JSON.
    """
    def handle(self):
        global server_instance
        addr = self.client_address[0]
        self.request.settimeout(10)
        try:
            data = self.request.recv(1024).strip()
            join_req = json.loads(data.decode('utf-8'))
            if join_req.get("type") == "join":
                client_name = join_req.get("name", "Unknown")
                accepted = confirm_join_request(client_name, addr)
                if accepted:
                    msg = f"Join request from {client_name} ({addr}) accepted."
                    self.request.sendall(json.dumps({"type": "join", "status": "accepted"}).encode('utf-8'))
                    if server_instance and server_instance.bridge:
                        server_instance.bridge.updateConsole(msg)
                    server_instance.add_client(self.request)
                else:
                    msg = f"Join request from {client_name} ({addr}) rejected."
                    self.request.sendall(json.dumps({"type": "join", "status": "rejected"}).encode('utf-8'))
                    if server_instance and server_instance.bridge:
                        server_instance.bridge.updateConsole(msg)
                    return  # End connection
            # Listen for further messages
            while True:
                data = self.request.recv(1024).strip()
                if not data:
                    break
                try:
                    msg_json = json.loads(data.decode('utf-8'))
                    if msg_json.get("type") == "message":
                        content = msg_json.get("content", "")
                        log = f"[{addr}] {content}"
                        print(log)
                        if server_instance:
                            server_instance.broadcast_message(log)
                except Exception as e:
                    print("Error processing client message:", e)
        except Exception as e:
            print(f"Client {addr} error/disconnected: {e}")
        finally:
            if server_instance:
                server_instance.remove_client(self.request)
            print(f"Connection closed for {addr}")

def confirm_join_request(client_name, addr):
    """
    Shows a modal dialog in the GUI thread asking the host to accept the join request.
    This function blocks until the host responds.
    """
    result_holder = {}
    event_loop = QEventLoop()

    def ask():
        ret = QMessageBox.question(main_window, "Join Request",
                                   f"Accept join request from {client_name} ({addr})?",
                                   QMessageBox.Yes | QMessageBox.No)
        result_holder["accepted"] = (ret == QMessageBox.Yes)
        event_loop.quit()

    QTimer.singleShot(0, ask)
    event_loop.exec_()
    return result_holder.get("accepted", False)

# --------------------- Bridge Object for QWebChannel -------------------------

class Bridge(QObject):
    """
    Exposes methods to JavaScript via QWebChannel.
    HTML/JS can call these methods to control the server, TTS, and discovery.
    """
    updateOutputSignal = pyqtSignal(str)
    updateConsoleSignal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    @pyqtSlot()
    def startServer(self):
        """Starts a local server (if not already running)."""
        global server_instance
        if server_instance:
            self.updateConsole("Server already running.")
            return
        HOST = "0.0.0.0"
        PORT = 12345
        try:
            server_instance = LocalServer((HOST, PORT), ClientHandler, bridge=self)
            threading.Thread(target=server_instance.serve_forever, daemon=True).start()
            self.updateConsole(f"Server started on {HOST}:{PORT}")
        except Exception as e:
            self.updateConsole(f"Error starting server: {e}")

    @pyqtSlot()
    def shutdownServer(self):
        """Shuts down the local server."""
        global server_instance
        if server_instance:
            server_instance.shutdown()
            server_instance.server_close()
            self.updateConsole("Server shut down.")
            server_instance = None
        else:
            self.updateConsole("No server to shut down.")

    @pyqtSlot()
    def discoverServers(self):
        """Discover available local servers using UDP broadcast discovery."""
        threading.Thread(target=self._discover_servers_thread, daemon=True).start()

    def _discover_servers_thread(self):
        servers = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow address reuse for UDP discovery
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", BROADCAST_PORT))
        except Exception as e:
            self.updateConsole(f"UDP discovery bind error: {e}")
            return
        sock.settimeout(3)
        start_time = time.time()
        while time.time() - start_time < 3:
            try:
                data, addr = sock.recvfrom(1024)
                server_info = json.loads(data.decode('utf-8'))
                if server_info.get("type") == "server_announcement":
                    # Use the sender's IP as the host address
                    server_info["host"] = addr[0]
                    # Avoid duplicate entries
                    if not any(s.get("host") == server_info["host"] and s.get("port") == server_info["port"] for s in servers):
                        servers.append(server_info)
            except socket.timeout:
                break
            except Exception as e:
                print("Discovery error:", e)
        sock.close()
        self.updateConsole(f"Discovered servers: {servers}")
        js = f"populateServerList({json.dumps(servers)});"
        if main_window:
            main_window.web_view.page().runJavaScript(js)

    @pyqtSlot(str)
    def connectToServer(self, server_info_str):
        """Connect as a client to a server using provided server info (JSON string)."""
        global client_instance
        server_info = json.loads(server_info_str)
        host = server_info.get("host")
        port = server_info.get("port")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            join_req = {"type": "join", "name": "Client"}
            s.sendall(json.dumps(join_req).encode('utf-8'))
            data = s.recv(1024)
            response = json.loads(data.decode('utf-8'))
            if response.get("type") == "join" and response.get("status") == "accepted":
                self.updateConsole(f"Connected to server at {host}:{port}")
                client_instance = s
                threading.Thread(target=client_listener_thread, args=(s,), daemon=True).start()
            else:
                self.updateConsole("Join request rejected by server.")
                s.close()
        except Exception as e:
            self.updateConsole("Error connecting to server: " + str(e))

    @pyqtSlot(str)
    def selectTTSVoice(self, voice_id):
        """Selects the TTS voice based on the provided voice ID."""
        global current_voice_id
        current_voice_id = voice_id
        self.updateConsole(f"TTS voice set to: {voice_id}")

    @pyqtSlot(str)
    def updateConsole(self, text):
        """Emit a signal to update the console area in HTML."""
        self.updateConsoleSignal.emit(text)

    @pyqtSlot(str)
    def receiveMessage(self, text):
        """(Optional) Receive a message from the HTML side."""
        self.updateConsole(f"Received from HTML: {text}")

# --------------------- Client Listener for Client Connections ----------------

def client_listener_thread(client_socket):
    """Listens for messages from the server when acting as a client."""
    global client_instance
    try:
        while True:
            data = client_socket.recv(1024)
            if not data:
                break
            try:
                msg_json = json.loads(data.decode('utf-8'))
                if msg_json.get("type") == "message":
                    content = msg_json.get("content", "")
                    if main_window:
                        main_window.bridge.updateConsole(f"Server: {content}")
                    speak_message(content)
            except Exception as e:
                print("Error in client receive:", e)
    except Exception as e:
        print("Client listener error:", e)
    finally:
        client_instance = None

# ----------------------- MainWindow and GUI Setup -----------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Python GUI: Key Logger, Local Server & Discovery")
        self.setGeometry(100, 100, 900, 700)
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)
        self.bridge = Bridge()
        self.setup_webchannel()
        self.load_html()
        # Set up a QTimer to throttle UI updates for the typed message
        self.output_update_timer = QTimer(self)
        self.output_update_timer.timeout.connect(self.update_output_display)
        self.output_update_timer.start(200)  # update every 200 ms

    def setup_webchannel(self):
        """Sets up QWebChannel for JS-Python communication."""
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)
        self.bridge.updateConsoleSignal.connect(self.run_js_updateConsole)

    def load_html(self):
        """Loads the modern HTML page with controls for server discovery and connection."""
        html_content = r"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <title>Modern Key Logger & Local Server</title>
          <style>
            body {
              margin: 0;
              padding: 0;
              font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
              background: linear-gradient(135deg, #6e8efb, #a777e3);
              color: #fff;
              overflow: auto;
            }
            .container {
              display: flex;
              flex-direction: column;
              align-items: center;
              padding: 20px;
            }
            #output {
              font-size: 1.8em;
              background: rgba(0, 0, 0, 0.3);
              padding: 15px;
              border-radius: 10px;
              margin: 10px;
              width: 80%;
              min-height: 50px;
            }
            #console {
              font-size: 1em;
              background: rgba(0, 0, 0, 0.5);
              padding: 10px;
              border-radius: 10px;
              margin: 10px;
              width: 80%;
              height: 150px;
              overflow-y: auto;
            }
            button, select {
              margin: 5px;
              padding: 10px;
              font-size: 1em;
              border: none;
              border-radius: 5px;
              cursor: pointer;
            }
            button:hover, select:hover {
              opacity: 0.8;
            }
          </style>
        </head>
        <body>
          <div class="container">
            <h1>Modern Key Logger, Local Server & Discovery</h1>
            <div id="output">Typed text will appear here...</div>
            <div>
              <button onclick="bridge.startServer()">Start Server</button>
              <button onclick="bridge.shutdownServer()">Shutdown Server</button>
              <button onclick="bridge.discoverServers()">Discover Servers</button>
            </div>
            <div>
              <select id="serverSelect">
                <option value="">Select a server</option>
              </select>
              <button onclick="connectToSelectedServer()">Connect to Server</button>
            </div>
            <div>
              <select id="voiceSelect" onchange="selectVoice()">
                <option value="">Default Voice</option>
              </select>
            </div>
            <div id="console">Console output...</div>
          </div>
          <script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
          <script>
            var bridge = null;
            new QWebChannel(qt.webChannelTransport, function(channel) {
              bridge = channel.objects.bridge;
            });
            function updateOutput(text) {
              document.getElementById('output').innerText = text;
            }
            function updateConsole(text) {
              var consoleElem = document.getElementById('console');
              consoleElem.innerHTML += "<br>" + text;
              consoleElem.scrollTop = consoleElem.scrollHeight;
            }
            function populateVoices(voices) {
              var select = document.getElementById('voiceSelect');
              select.innerHTML = "<option value=''>Default Voice</option>";
              voices.forEach(function(v) {
                var option = document.createElement("option");
                option.value = v.id;
                option.text = v.name;
                select.appendChild(option);
              });
            }
            function populateServerList(servers) {
              var select = document.getElementById('serverSelect');
              select.innerHTML = "<option value=''>Select a server</option>";
              servers.forEach(function(s) {
                var option = document.createElement("option");
                option.value = JSON.stringify(s);
                option.text = s.name + " (" + s.host + ":" + s.port + ")";
                select.appendChild(option);
              });
            }
            function selectVoice() {
              var voiceId = document.getElementById('voiceSelect').value;
              if(bridge) {
                bridge.selectTTSVoice(voiceId);
              }
            }
            function connectToSelectedServer() {
              var select = document.getElementById('serverSelect');
              var server_info = select.value;
              if (server_info && bridge) {
                bridge.connectToServer(server_info);
              }
            }
          </script>
        </body>
        </html>
        """
        self.web_view.setHtml(html_content, QUrl("qrc:///"))

    def run_js_updateConsole(self, text):
        """Run JS function to update the console area."""
        js = f"updateConsole({json.dumps(text)});"
        self.web_view.page().runJavaScript(js)

    def update_output_display(self):
        """Periodically update the displayed typed message (throttled)."""
        with message_lock:
            current_text = typed_message
        js = f"updateOutput({json.dumps(current_text)});"
        self.web_view.page().runJavaScript(js)

# ------------------- Global Key and Mouse Listener Functions -----------------

def on_key_press(key):
    """Global key listener to capture keystrokes and handle backspace and space."""
    global typed_message
    try:
        with message_lock:
            if hasattr(key, 'char') and key.char is not None:
                typed_message += key.char
            elif key == keyboard.Key.space:
                typed_message += " "
            elif key == keyboard.Key.backspace:
                typed_message = typed_message[:-1]
    except Exception as e:
        print("Keyboard error:", e)

def on_mouse_click(x, y, button, pressed):
    """On middle mouse click, send the typed message either via server or client."""
    global typed_message
    if button == mouse.Button.middle and pressed:
        with message_lock:
            message_to_send = typed_message
            typed_message = ""
        if main_window:
            main_window.run_js_updateConsole("")  # Clear output display if needed
        if message_to_send.strip() == "":
            return
        if server_instance:
            server_instance.broadcast_message("Host: " + message_to_send)
        elif client_instance:
            try:
                payload = json.dumps({"type": "message", "content": "Client: " + message_to_send}).encode('utf-8')
                client_instance.sendall(payload)
            except Exception as e:
                print("Client send error:", e)
                speak_message("Error sending message.")
        else:
            speak_message("Client: " + message_to_send)

def start_listeners():
    """Start global keyboard and mouse listeners in separate threads without blocking."""
    kb_listener = keyboard.Listener(on_press=on_key_press)
    ms_listener = mouse.Listener(on_click=on_mouse_click)
    kb_listener.start()
    ms_listener.start()
    # Do not call join() here so that these threads run in the background.

# -------------------- Main Application Entry Point ----------------------------

if __name__ == '__main__':
    app = QApplication(sys.argv)
    global main_window
    main_window = MainWindow()
    main_window.show()

    # Pre-populate TTS voices in HTML after ensuring QWebChannel is ready.
    voices = tts_engine.getProperty('voices')
    voices_list = [{"id": v.id, "name": v.name} for v in voices]
    def populate_voices():
        js = f"populateVoices({json.dumps(voices_list)});"
        main_window.web_view.page().runJavaScript(js)
    threading.Timer(2.0, populate_voices).start()

    # Start the global key and mouse listeners in a background thread.
    threading.Thread(target=start_listeners, daemon=True).start()

    sys.exit(app.exec_())
