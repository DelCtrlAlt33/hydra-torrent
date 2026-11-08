# Storage and NAS setup

Optional. Only matters if your library lives on a NAS reached by more than one protocol.

[Back to the README](../README.md)

Only matters if your media library lives on a NAS (Synology, TrueNAS, Unraid, plain Samba, whatever) that more than one machine or protocol connects to - e.g. Windows over SMB, and a Linux box or Docker host over NFS.

**The rule:** if the same folder is reachable over multiple protocols, they need to resolve to the *same user identity* on the NAS side. If SMB writes as one user and NFS writes as a different one (a very common default - NFS often defaults to an anonymous `nobody` user unless you set it explicitly), files created by one protocol can become invisible or undeletable from the other. Everything still *works* at first, then folders quietly fill up with things nobody can clean up.

Where to fix it depends on your NAS software, but the concept is the same everywhere: whatever user your Windows/SMB connection uses, make sure NFS exports for the same folder map to that identical user.
- **TrueNAS SCALE:** NFS share → Edit → **Mapall User / Mapall Group**, set to match your SMB user.
- **Synology:** Control Panel → Shared Folder → NFS Permissions → the "Squash" setting controls this.
- **Plain Samba + `/etc/exports`:** use `anonuid=`/`anongid=` matching your Samba user's UID/GID, with `all_squash`.

If everything reaching your NAS uses the *same* protocol (e.g. only Docker containers over NFS, nothing over SMB), this doesn't apply to you at all - skip it.
