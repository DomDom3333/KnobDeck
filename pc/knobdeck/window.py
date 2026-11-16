"""Settings window (own process, because pywebview needs the main thread).
Falls back to the web browser if no window toolkit is available (e.g. Linux without GTK WebKit or Qt)."""
import sys
import webbrowser


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7413/"
    try:
        import webview
        webview.create_window("KnobDeck settings", url, width=1280, height=820, min_size=(1000, 680))
        webview.start()
    except Exception as e:
        print(f"no settings window ({e}) - opening the browser", file=sys.stderr)
        webbrowser.open(url)


if __name__ == "__main__":
    main()
