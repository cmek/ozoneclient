# Ozone integration tests

These are **live** integration tests against a real Ozone **DEV** environment.
They are driven entirely by environment variables; any test whose required
variables are missing is **skipped**, so a partial config still runs a useful
subset. See `TEST_PLAN.md` (repo root) for the case-by-case rationale.

The suite is split by cost (mirrors the test plan):

- **Part 1 — `test_part1_validation.py`** — reads, auth, and client-side guard
  rails. Creates/modifies **nothing** in Ozone. Safe to run frequently / in CI.
- **Part 2 — `test_part2_lifecycle.py`** — create / activate / cancel. **Creates
  real Ozone objects (expensive).** Skipped unless `OZONE_RUN_MUTATING=1`.

## Setup

```bash
uv sync --group dev      # installs pytest
```

## Environment variables

### Core connection (required for everything)
| Variable | Notes |
|----------|-------|
| `OZONE_URI` | host[:port] of the DEV API |
| `OZONE_USERNAME` / `OZONE_PASSWORD` | service account creds |
| `OZONE_APP_ID` | application id header |
| `OZONE_APP_NAME` | optional, defaults to `ACX` |
| `OZONE_SCHEME` | optional, defaults to `http` |

### Part 1 fixtures
| Variable | Used by |
|----------|---------|
| `OZONE_TEST_USERNAME` | lookups + guard rails (resolves to a contact id and client GUID) |
| `OZONE_KNOWN_SO_ID` | P1-B5 read of a pre-existing SO |
| `OZONE_USERNAME_NO_CLIENT` | *(optional)* P1-G2 — a user with a contact but no `Client.RefGUID` |
| `OZONE_SO_NO_ACCOUNT` | *(optional)* P1-G4 — an SO with no resolvable `Account.RefGUID` |

### Part 2 fixtures (only when `OZONE_RUN_MUTATING=1`)
| Variable | Used by |
|----------|---------|
| `OZONE_PHYSICAL_SO` | create |
| `OZONE_LOCATION` | create (`CT1` / `CT2` / `JB1`) |
| `OZONE_SERVICE_CODE` | create (e.g. `VAX004`) |
| `OZONE_PARTYB_PHYSICAL_SO` | standard activation |
| `OZONE_PARTYA_VLANID` / `OZONE_PARTYB_VLANID` | standard activation |
| `OZONE_AWS_VLANID` / `OZONE_AWS_DXCON_ID` / `OZONE_AWS_ACCOUNT_ID` | AWS activation |
| `OZONE_AZURE_VLANID` / `OZONE_AZURE_SERVICE_KEY` | Azure ExpressRoute activation |

### Run gates
| Variable | Effect |
|----------|--------|
| `OZONE_RUN_MUTATING=1` | enable Part 2 (object-creating tests) |
| `OZONE_RUN_NEGATIVE=1` | enable the exploratory write-rejection negatives in Part 2 |

## Running

```bash
# Part 1 only (cheap, no objects created)
uv run pytest tests/test_part1_validation.py

# Part 2 (creates real DEV objects — run deliberately, infrequently)
OZONE_RUN_MUTATING=1 uv run pytest tests/test_part2_lifecycle.py

# everything, with the mutating + exploratory-negative paths
OZONE_RUN_MUTATING=1 OZONE_RUN_NEGATIVE=1 uv run pytest
```

## Notes

- Part 2 tests create exactly one SO each and **cancel it in teardown** (best
  effort) so DEV does not accumulate orphans.
- The exact Ozone response schema is not yet pinned down. Part 2 logs each
  response and marks the schema-dependent assertions with `TODO:` — fill those in
  (and the `so_id()` key in `conftest.py`) once you have observed real responses.
- Run with `-s --log-cli-level=INFO` to see the logged request/response detail.
