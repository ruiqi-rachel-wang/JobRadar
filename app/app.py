"""JobRadar app entrypoint.

The implementation lives in the api package so source fetchers, storage,
shared parsing helpers, and Flask routes are easier to navigate.
"""

import os

from api.main import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"\n🚀  JobRadar v4 → http://localhost:{port}\n")
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
