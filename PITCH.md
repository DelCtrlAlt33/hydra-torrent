# Hydra Torrent

A BitTorrent client, built for your media server.

## The idea

If you run Plex or Jellyfin, a download usually isn't the end of the job. Something has to find it, something has to grab it, and then it still needs to be renamed, filed into the right library folder and scanned. Plenty of good tools handle those steps, and many people run them together.

Hydra puts the whole path in one app: search, download, RSS, filing and the library scan. You add a show, and it shows up in your library.

## Who it's for

- People who run Plex or Jellyfin at home and want fewer moving parts.
- People who want a VPN kill switch built into the client rather than bolted on.
- People who like software they can read, run and change themselves. Hydra is open source under the MIT licence.

## What it does

- **One app for the whole path.** Search, download, organise and scan, all in the same place.
- **Search with your own indexers.** Hydra connects to your Jackett and ships with no indexers of its own, so every source is your choice.
- **RSS rules.** New episodes are picked up as they appear.
- **Automatic filing.** Movies, TV and anime go into the right folders, and Plex and Jellyfin are told to scan.
- **VPN kill switch.** Torrent traffic is bound to your VPN interface, and if the tunnel drops, every transfer pauses. Everything resumes when the VPN is back.
- **Runs where you want it.** A Windows app with a tray icon, a Linux service, or Docker, with a browser UI for all of them.
- **Little to configure.** It works out of the box. Jackett and Plex or Jellyfin are optional and can be added later.
- **A REST API with live updates**, so you can script it or connect it to other tools.

## What it is not

Hydra is its own client. It is built on libtorrent, the same library behind several well-known clients, but it does not mimic their web APIs, so tools that expect those will not connect to it as a download client. There is no login screen yet, so it belongs on your own network or behind a VPN, not on the open internet.

## Where it's going

Hydra is young and actively developed. The goal is simple: make the path from "I want to watch this" to "it's in my library" as short as it can be, without asking you to run a stack of separate services to get there.
