"""Read a Studio workbook from stdin, emit only verified channel totals."""
import json
import sys
from .zen_dzen_xlsx import MAX_BYTES, import_report


def compact_report(data, channel, channel_id):
    report = import_report(data, channel, channel_id)
    main, dzen = report["all_platforms"], report["dzen_platform"]
    return {"channel": channel, "channelId": channel_id,
            "digest": report["snapshot_id"], "start": main["start"], "end": main["end"],
            "all": main["totals"], "dzen": dzen["totals"]}


if __name__ == "__main__":
    try:
        channel, channel_id = sys.argv[1:]
        print(json.dumps(compact_report(sys.stdin.buffer.read(MAX_BYTES + 1), channel, channel_id),
                         separators=(",", ":"), allow_nan=False))
    except Exception:
        raise SystemExit(1)
