# Search and Auto-Download

Optional. Needed only for the Search tab and Auto-DL rules.

[Back to the README](../README.md)

Hydra searches torrents through [Jackett](https://github.com/Jackett/Jackett) - a free app that connects to hundreds of torrent sites and gives Hydra one place to search them all. If you want search or auto-download rules to work, you need Jackett running somewhere on your network.

1. Install Jackett ([instructions](https://github.com/Jackett/Jackett#installation)) - it runs on Windows, Linux, or Docker
2. Add some indexers (torrent sites) in Jackett's web UI
3. Copy your Jackett API key from the top of Jackett's dashboard
4. In Hydra's Settings tab, paste the Jackett URL (e.g. `http://localhost:9117`) and API key

That's it - the Search tab and Auto-DL rules will work. If you don't use Jackett, everything else still works fine - you just add torrents manually via magnet links or .torrent files.
