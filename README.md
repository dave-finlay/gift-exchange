# gift-exchange

A small, invite-only web app for running gift exchanges ("Secret Santa",
"Kris Kindle"): everyone in a group is secretly assigned one other person
to buy a gift for. It's also the vehicle for an experiment in automated
security: dependency updates, CVE scanning, rebuilds and redeploys.

It's meant to be self-hosted. If you're technically minded, you can fork
this repository and run your own private gift-exchange site for your
family, friends or community: one small server, a few euros a month, no
ads, and your group's data stays on your own server. Then change it to
suit your group.
It's early days: the draw itself works, and the web app is being built.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                    # create .venv and install dependencies
uv run pytest              # run the tests
uv run ruff check .        # lint
uv run ruff format .       # format
```

## Layout

| Path | What's in it |
|---|---|
| `src/giftexchange/matching.py` | The draw: fair random assignments under the exchange's rules, feasibility explanations, test-mode statistics and repairs. Pure Python with no dependencies. |
| `tests/` | Tests, including property-based tests with Hypothesis |
| `docs/DESIGN.md` | Architecture, data model and screens |

## License

[MIT](LICENSE)
