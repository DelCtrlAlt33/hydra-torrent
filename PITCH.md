# Hydra Torrent

## The Problem

Torrenting used to be simple. Open LimeWire, search for something, click it, done. That was it.

Now look at what it takes. The *arr stack is 7 containers, YAML configs, API keys wired between your own services, and a weekend of setup just to automate downloads. It works - but it's built for people who enjoy configuring infrastructure, not for people who just want to download something.

The torrent clients themselves haven't moved forward either. qBittorrent is just uTorrent without the malware. It held the line, but that's all it did.

Meanwhile streaming is falling apart. Prices keep going up, content is split across 10 platforms, shows disappear without warning. People are coming back to torrenting and finding out they need a computer science degree to get started.

## The Pitch

Remember when downloading was simple? Search, click, done. Hydra brings that back - with modern automation built in. No 7-container stack. No weekend of configuration.

## The Numbers

The *arr stack is roughly 800,000 lines of code across 7 containers. Hydra is 7,000 lines and one container. You can read the entire codebase in an afternoon. Try that with Sonarr.

## Who It's For

- People who grew up with LimeWire and uTorrent and miss that simplicity
- People who tried the *arr stack and gave up on day two
- People who just want to search for something and have it show up in Plex
- People who are sick of paying for 6 streaming services and still can't find what they want

## What Makes Hydra Different

- **All-in-one.** Search, download, organize - one app. Not 7 services duct-taped together.
- **One container.** `docker run hydra` and you're done.
- **Zero required config.** Works out of the box. Jackett API key for search and Plex token for library updates are both optional.
- **Built-in search.** Search → click → it lands in your library. No separate Overseerr.
- **Media organization.** Downloads finish, files get renamed and moved. Plex picks them up automatically.
- **VPN guard built in.** Not a separate container.
- **Themeable.** Swap the look without touching config files.
- **API-first.** Full REST API with real-time WebSocket updates. Build a Discord bot, a mobile app, a CLI tool, hook it into Home Assistant - Hydra doesn't care. Most torrent clients bolted their API on as an afterthought. Hydra was built on one.
- **7,000 lines.** One person can read the whole codebase in an afternoon. Try that with Sonarr.

## The Vision

The last real innovation in this space was qBittorrent replacing uTorrent. That was over a decade ago. Hydra is the next step - and it's not adding more containers. It's collapsing the whole stack into one app that anyone can use.
