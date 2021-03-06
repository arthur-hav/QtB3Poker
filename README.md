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

## Contributing

In this project more than many others, it is possible to contribute various ways that do not imply coding.

Whether or not you would like to submit new ideas, provide better or alternative assets, or code, you are invited to follow theses steps:

1. Browse issues to see if your idea of improvement is already in the roadmap.
2. If it is, interact with it by commenting or upvoting what you would support.
3. If not, create one, I welcome and openly discuss all kind of innovative changes.

Additionally, if you intend to provide code:

1. If you know how to code but are stuck on implementing a given issue, request for additional specification in the issue and I will provide help. The code and issues I open are commented with the bare minimum.
2. Then, submit a pull request with the changes associated to the issue. I will review it and integrate it; do not be shy as I welcome just about anything that completes an open issue without breaking stuff.

### Code standards

Code that works
