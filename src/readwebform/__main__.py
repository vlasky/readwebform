"""
Allow readwebform to be executed as a module: python -m readwebform
"""

import sys
from readwebform.cli import main

if __name__ == '__main__':
    sys.exit(main())
