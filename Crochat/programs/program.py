#!/usr/bin/env py
"""
Simple Python launcher for a PyQt5 application with web engine, input control, and text-to-speech support.

This script checks if PyQt5, PyQtWebEngine, pynput, and pyttsx3 are installed.
If not, it will install them using pip.
After ensuring all required packages are installed, it runs the main.py program.
If main.py encounters an error, the error message is printed to the console.

WARNING:
- This code is for educational purposes only.
- Do not use this script to perform unethical activities or to bypass security measures.
- Always review external code for security implications and proper licensing.
"""

import subprocess
import sys

def install_package(package_name):
    """
    Installs a Python package using pip.
    
    Warning:
    - Running pip from within a script may require proper permissions.
    """
    print(f"Installing {package_name}...")
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', package_name])
        print(f"{package_name} installation complete.")
    except subprocess.CalledProcessError as install_error:
        print(f"Failed to install {package_name}. Please check your network connection and permissions.")
        print("Error details:", install_error)
        sys.exit(1)

# Check if PyQt5 is installed
try:
    import PyQt5
    print("PyQt5 is already installed.")
except ImportError as e:
    print("PyQt5 is not installed. Error:", e)
    install_package('PyQt5')

# Check if PyQtWebEngine is installed
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    print("PyQtWebEngine is already installed.")
except ImportError as e:
    print("PyQtWebEngine is not installed. Error:", e)
    install_package('PyQtWebEngine')

# Check if pynput is installed
try:
    import pynput
    print("pynput is already installed.")
except ImportError as e:
    print("pynput is not installed. Error:", e)
    install_package('pynput')

# Check if pyttsx3 (Text-to-Speech) is installed
try:
    import pyttsx3
    print("pyttsx3 is already installed.")
except ImportError as e:
    print("pyttsx3 is not installed. Error:", e)
    install_package('pyttsx3')

# Optionally, a simple math calculation for demonstration (Area of a circle)
import math
radius = 5  # example radius
area = math.pi * (radius ** 2)
print(f"Math demo: The area of a circle with radius {radius} is approximately {area:.2f}")

# Run main.py and capture output/errors
print("Running main.py...")
try:
    result = subprocess.run(
        [sys.executable, 'main.py'],
        check=True,
        capture_output=True,
        text=True
    )
    print("main.py output:")
    print(result.stdout)
except subprocess.CalledProcessError as run_error:
    print("An error occurred while running main.py:")
    print(run_error.stderr)
    sys.exit(1)
