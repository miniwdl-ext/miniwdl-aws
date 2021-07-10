import sys
from .cli_submit import miniwdl_submit_awsbatch


def main(args=sys.argv):
    miniwdl_submit_awsbatch(args)


if __name__ == "__main__":
    sys.exit(main())
