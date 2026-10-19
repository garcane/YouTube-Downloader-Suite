
from ytdl.core import fetch_info, download
import sys

def reproduce():
    url = "https://www.youtube.com/watch?v=gsUqW1IookY"
    print(f"Fetching info for {url}...")
    try:
        info = fetch_info(url)
        print(f"Title: {info.title}")

        # Look for 4K (2160p) option
        option_4k = None
        for opt in info.options:
            if opt.resolution == "2160p":
                option_4k = opt
                break

        if not option_4k:
            print("4K option not found. Available options:")
            for opt in info.options:
                print(f" - {opt.label} ({opt.resolution})")
            return

        print(f"Attempting to download {option_4k.label}...")
        # This is where the 403 is expected
        result = download(url, option_4k, info=info)
        print(f"Successfully downloaded to: {result.path}")

    except Exception as e:
        print(f"\nCaught expected error:\n{e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    reproduce()
