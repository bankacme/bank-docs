import sys
from openapi_spec_validator import validate as validate_spec
from openapi_spec_validator.readers import read_from_filename
ok = True
for f in sys.argv[1:]:
    try:
        spec, url = read_from_filename(f)
        validate_spec(spec, base_uri=url)
        print("OK  ", f)
    except Exception as e:
        ok = False
        print("FAIL", f, type(e).__name__, str(e)[:300])
sys.exit(0 if ok else 1)
