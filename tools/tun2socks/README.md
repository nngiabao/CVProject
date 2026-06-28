# tun2socks runtime

Place the Windows tun2socks runtime files here:

- `tun2socks.exe`
- `wintun.dll`

The app also accepts `TUN2SOCKS_EXE` as an environment variable pointing to
`tun2socks.exe`.

If tun2socks needs a specific outbound Windows adapter, set:

```powershell
$env:TUN2SOCKS_INTERFACE = "Your adapter name"
```

Do not store proxy lists or proxy credentials in this folder.
