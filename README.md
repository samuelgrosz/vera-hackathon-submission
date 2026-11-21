<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/vera-logo-reversed.svg">
    <img alt="Vera" src="assets/brand/vera-logo.svg" width="280">
  </picture>

  <h1>Vera</h1>
  <p>Track 2 · Same Person or Not?</p>
  <p><a href="https://samuelgrosz.co.uk/vera-hackathon-submission/">Vera website</a></p>
</div>

Vera checks whether a watchlist hit describes a bank’s customer. It clears a hit when the evidence establishes a different person or company, confirms matches the evidence supports, and gives analysts a reason and source records for cases that remain open.

## Run the evaluator

```sh
system/run /path/to/screening/dev output/dev.jsonl
```

The default runner uses Python 3.10+ and the standard library. It runs offline. The organizer’s inputs are provided separately.

- [Two-minute demo video](https://github.com/samuelgrosz/vera-hackathon-submission/releases/download/hackathon-demo/Vera-Track-2-Demo.mp4)
- [Evaluator and reproducibility details](system/README.md)
- [Validated development predictions](output/dev.jsonl)
- Fictional reviewer demo via `tools/review --demo`
- Analyst terminal app via `./vera /path/to/screening/dev`
