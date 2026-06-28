# Tqk WinDivert redirector runtime

Put the built Tqk redirector files in this folder:

- `TqkLibrary.WinDivert.Demo.exe`
- `WinDivert.dll`
- `WinDivert64.sys`
- all `.dll` files produced beside the demo exe

The app starts the helper like this for each LDPlayer PID:

```powershell
TqkLibrary.WinDivert.Demo.exe proxy --proxy socks5://host:port --process <pid> --follow-children --secure-dns --doh https://1.1.1.1/dns-query --exit-when-process-gone
```

You can also point to another build with:

```powershell
$env:TQK_REDIRECTOR_EXE="C:\path\to\TqkLibrary.WinDivert.Demo.exe"
```

Run the Python app as Administrator so WinDivert can attach.
