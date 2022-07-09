import sys
import os.path
import site
import supervisor.supervisord


def main():
    # kyzylborda.supervisor.scripts.supervisord, hence four ..'s.
    kyzylborda_path = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    site.addsitedir(kyzylborda_path)
    sys.exit(supervisor.supervisord.main())


if __name__ == "__main__":
    main()
