# Troubleshooting

[Back to the README](../README.md)

### Useful commands (Linux)

```bash
sudo systemctl status hydra-torrent     # check if it's running
sudo systemctl restart hydra-torrent    # restart
sudo journalctl -u hydra-torrent -f     # view logs
```

### Troubleshooting

**`pip install libtorrent` fails?** Make sure you're on Python 3.10-3.13. Older versions don't have prebuilt packages. If you're on Linux and pip doesn't work, try `sudo apt install python3-libtorrent` instead.

**Can't connect?** Hydra runs on port 8765. Make sure nothing else is using that port. Check your firewall if accessing from another device on your network.
