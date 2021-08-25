# QtB3Poker

This is a public implementation of a poker client and server, in pyQt5, playing Bordeaux3 poker variant

![Poker software screenshot](https://raw.githubusercontent.com/arthur-hav/QtB3Poker/main/images/showdown.PNG)

## Dependencies

### Client

The only requirements are from pip packages listed under client-requirements.txt and can be installed using `pip install -r client-requirements.txt`

#### Building windows client executable

```
pip install pyinstaller
python -m PyInstaller -F client.py
```

AVOID building with a different arch python than your system arch. Such as Pycharm venv python. 
This is the primary problem of detecting the binary as malicious by antivirus sofware such as Windows defender.

Resulting exe is under `./dest`. The executable will need the assets
under the images and sounds directories.

### Server

The server uses the python3 port of the deuces library. https://github.com/arthur-hav/deuces

You will also need a local running instance of MongoDB.

Other requirements are listed in server-requirements.txt and can be installed 
using `pip install -r server-requirements.txt`

Running under supervisor is advised.

