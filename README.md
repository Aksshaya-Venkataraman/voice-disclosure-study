# Impact of Voice on Disclosure

A conversational interviewer for a within-subject study: **does the voice of an
interviewer change how much people disclose?** Participants take a spoken
interview in four topic blocks. Each block is asked by a different voice
(*William*, *Megan*, *Pixie* and a *Robot*). Their answers are transcribed and
then analysed with pandas, NumPy and SciPy.

- **Rasa 3.6** runs the dialogue. NLU handles the warm-up small talk, and a custom action steps through the scripted interview.
- **Google Cloud Speech-to-Text** transcribes spoken answers. The audio is never stored.
- **FastAPI gateway** handles participant links, consent, answers, withdrawal and the researcher API, and serves the participant web app.
- **Balanced Latin square** counterbalances which voice asks which block.
- **Analysis package** computes disclosure measures and within-subject statistics, and writes a report with figures.
- **Production deployment**: Docker Compose behind Caddy (automatic HTTPS) with PostgreSQL. Everything except Caddy runs on internal networks.

> **No study data is included.** All numbers below come from the bundled
> *synthetic* data generator, which exists to test the pipeline and to plan
> sample sizes. They are not research findings.

## How a session works

```
participant browser ──HTTPS──▶ Caddy ──▶ gateway (FastAPI) ──▶ Rasa ──▶ action server
   speaks / types                          │   │                      (interview/script.py)
                                           │   └──▶ Google Speech-to-Text (optional)
                                           └──▶ PostgreSQL
```

1. The researcher creates participant links. Each link holds a 256-bit secret in the URL fragment, so it never reaches server logs. The link is shown once and only its SHA-256 hash is stored.
2. The participant confirms they are an adult and gives consent. They also say whether English is their native language, which the analysis uses as a covariate.
3. **Warm-up.** Three small-talk turns, such as "How are you doing today?". Rasa NLU classifies the replies (mood, yes/no), which chooses the next line.
4. **Interview.** Four blocks (technical, marketing, financial, healthcare), each with four open questions. Each block is spoken by one voice, and the voice order follows a balanced 4×4 Latin square across participants. Every voice appears once in every position, and every voice follows every other voice exactly once.
5. **Answers.** Participants record each answer. The gateway sends the audio to Google Speech-to-Text and stores only the transcript, its confidence and the duration. Typing is always available as a fallback.
6. **Withdrawal.** Participants can withdraw at any time. All of their answers are deleted immediately.

The gateway keeps the interview state and sends it to Rasa on every turn, so a Rasa restart loses nothing. If Rasa is unreachable, the gateway uses the same script directly for that turn. The questions and their order stay identical; only the small-talk interpretation falls back to keywords.

### The four voices

`gateway/static/voices.js` renders the prompts with the browser's speech
synthesis, using four profiles (pitch, rate, preferred system voices). Browser
voices differ between devices. **For a controlled study, pre-render every
prompt** with a TTS service of your choice and place the files at
`gateway/static/audio/<voice>/<prompt_id>.mp3`, with
`gateway/static/audio/manifest.json` listing them. Recorded files always take
priority over browser synthesis. The interviewers are shown to participants
only as "Interviewer A–D".

## Repository layout

| Path | What it is |
|---|---|
| `interview/script.py` | The interview: blocks, questions, Latin square, state machine (shared by Rasa and the gateway) |
| `rasa/` | Rasa project: NLU data, rules, domain, the custom action, tests, Dockerfile, locked requirements |
| `gateway/` | FastAPI app, participant web app (`static/`), Alembic migrations, tests |
| `analysis/` | `disclosure_analysis` package: features, statistics, report CLI, synthetic data generator, tests |
| `deploy/` | Caddyfile and the PostgreSQL init script (least-privilege app role) |
| `scripts/` | `generate_env.py` (random secrets) and `smoke_test.py` (end-to-end check) |

## Deploying

Requirements: a Linux server with Docker and Docker Compose, a domain name that points at it, and ports 80 and 443 open.

```bash
git clone https://github.com/Aksshaya-Venkataraman/voice-disclosure-study.git
cd voice-disclosure-study
python3 scripts/generate_env.py --domain study.example.org --contact you@example.org
docker compose up -d --build
```

`generate_env.py` writes `.env` (mode 0600, git-ignored) with random database
passwords and the researcher token. Caddy obtains a Let's Encrypt certificate
automatically. Without speech recognition, participants type their answers.

### Turning on voice answers (Google Speech-to-Text)

1. In Google Cloud, enable the Speech-to-Text API and create a service account that has only the **Cloud Speech Client** role. Download its JSON key.
2. Save the key as `secrets/google-credentials.json` and restrict it to the gateway's container user:
   ```bash
   sudo chown 10001:10001 secrets/google-credentials.json
   sudo chmod 0400 secrets/google-credentials.json
   ```
3. Start the stack with the speech override. Either regenerate `.env` with `--speech` (which sets `COMPOSE_FILE`), or run:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.speech.yml up -d --build
   ```

The override also gives the gateway (and only the gateway) a network route to the internet, which it needs to reach Google.

### Running the study

```bash
TOKEN=$(grep '^RESEARCHER_TOKEN=' .env | cut -d= -f2-)

# Create 24 participant links (shown once - store them securely)
curl -s -X POST https://study.example.org/api/researcher/participants \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"count": 24}'

# Progress overview
curl -s https://study.example.org/api/researcher/participants -H "Authorization: Bearer $TOKEN"

# Export all answers (withdrawn participants are excluded)
curl -s https://study.example.org/api/researcher/export.csv -H "Authorization: Bearer $TOKEN" -o responses.csv
```

## Analysis

```bash
cd analysis
pip install --require-hashes -r requirements.txt
PYTHONPATH=.:.. python -m disclosure_analysis.report ../responses.csv --out report/
```

The report includes, for each disclosure measure:

- **Measures:** words per answer, content words (stop words removed), first-person references per 100 words, and emotion words per 100 words. Speech rate is added for timed voice answers.
- **Per voice:** participant-level means with bootstrap 95% confidence intervals.
- **Overall test:** a Friedman test across the four voices, with Kendall's W as the effect size.
- **Pairwise tests:** Wilcoxon signed-rank tests with Holm correction.
- **Mixed model:** a linear mixed model, `measure ~ voice + block + (1 | participant)`. It uses every answer and separates the voice effect from the topic effect.
- **Covariate:** answer length split by native and non-native English speakers.

It writes `report.md`, `results.json`, CSV tables and box plots.

To try it without data, or to plan a sample size, generate a synthetic export:

```bash
PYTHONPATH=.:.. python -m disclosure_analysis.simulate --participants 24 --out synthetic.csv
PYTHONPATH=.:.. python -m disclosure_analysis.simulate --participants 24 --no-effect --out null.csv
```

With the default *assumed* effects (Robot answers 18% shorter, Megan 8%
longer), 24 synthetic participants give Friedman χ²(3) = 21.1, p < 0.001.
The tests also confirm that the null simulation does not produce a
significant result.

## Development

```bash
pip install --require-hashes -r requirements-dev.txt     # Python 3.13
(cd gateway && pytest)        # 33 tests: API, auth, consent, withdrawal, export, headers, engines
(cd analysis && pytest)       # 11 tests: features, statistics, report CLI
ruff check . && ruff format --check . && bandit -c pyproject.toml -r gateway/gateway analysis/disclosure_analysis interview rasa/actions scripts
```

Rasa needs Python 3.10:

```bash
cd rasa
python3.10 -m venv .venv && .venv/bin/pip install --require-hashes --no-deps -r requirements.txt
.venv/bin/rasa data validate --fail-on-warnings
.venv/bin/rasa train && .venv/bin/rasa test core --stories tests/test_stories.yml
```

For local development without Rasa, set `INTERVIEW_ENGINE=scripted`.

### Continuous integration

Every push runs these jobs:

- **Lint and SAST:** ruff and bandit.
- **Dependency audit:** pip-audit on every lock file, plus a check that every lock file still matches its inputs (`scripts/check_locks.sh`).
- **Secret scan:** gitleaks over the full git history.
- **Tests:** the gateway and analysis suites.
- **Migrations:** Alembic upgrade, downgrade and upgrade again on PostgreSQL.
- **Docker end-to-end:**
  1. Build the three images. The Rasa build validates the training data, trains the model and runs the dialogue tests.
  2. Start the full stack.
  3. Run a complete interview over HTTPS through Caddy → gateway → Rasa → action server → PostgreSQL, with TLS verified against Caddy's CA.
  4. Check that Rasa answered every turn (no scripted fallback), that internal ports are unreachable and that the containers run as non-root.

## Security and privacy

See [SECURITY.md](SECURITY.md) for the controls, the threat model, data
protection (consent, withdrawal, retention), and the reviewed advisories in
Rasa's pinned dependencies.

## License

MIT - see [LICENSE](LICENSE).
