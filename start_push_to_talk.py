#!/usr/bin/env python3

import signal
import sys

from push_to_talk_realtime import main

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
