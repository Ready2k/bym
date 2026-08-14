# Third-party notices

The Apache License 2.0 in this repository applies to the Beat Your Mates
source code only. It does not grant rights in third-party services, data,
logos, club names, or trademarks.

## Python dependencies

The project declares the following runtime dependencies in `pyproject.toml`:

- NumPy
- pandas
- Requests
- SciPy

They are installed separately and remain subject to their own licences and
notices. Distributors are responsible for preserving the notices required by
the versions they ship.

## Football data and APIs

The application can obtain football data and related information from
third-party providers, including football-data.co.uk, football-data.org,
API-Football, The Odds API, Tavily, and other sources recorded in
`config/source_registry.json`. Their terms, quotas, attribution requirements,
and permitted-use restrictions apply independently. Supplying an API key or
distributing retrieved data does not make that material Apache-licensed.

## Club crests, names, and trademarks

The web interface links to club-crest image files hosted by football-logos.cc.
Those remote images are not bundled in this repository and are not licensed
under Apache-2.0 by this project. Club names, badges, and related marks remain
the property of their respective owners. Anyone deploying or redistributing
the application must review the relevant provider terms and obtain any
necessary permissions.
