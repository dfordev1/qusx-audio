# QUSX-Audio

Word-by-word audio for the Qur'an, addressed by [QUSX](https://github.com/dfordev1/usxv2)
word id.

QUSX describes the text. This adds sound to it, as a sidecar — no change to any
`.qusx.xml` file, and a text-only consumer is unaffected.

**Live:** `https://quran-wbw-audio.quran-wbw.workers.dev/en/v1/`

```
/index.json            manifest
/index/002.json        surah index — ids, clips, printed + spoken text
/audio/<clipId>.opus   the audio
```

## Try it

```js
const BASE = 'https://quran-wbw-audio.quran-wbw.workers.dev/en/v1';
const idx  = await (await fetch(`${BASE}/index/001.json`, {cache:'no-cache'})).json();

const clip = idx.words['1'];          // QUSX word id 1  =  بِسْمِ
console.log(idx.text[clip]);          // "In (the) name"  — as printed
console.log(idx.spoken?.[clip]);      // "In the name"    — what the audio says
new Audio(`${BASE}/audio/${clip}.opus`).play();
```

`examples/player.html` is a complete word-by-word reader in one file: Arabic pulled
live from the QUSX repo, audio and glosses from the endpoint above.

## What is here

| | |
|---|---|
| `spec/` | the QUSX-Audio 0.1 format, and its JSON schema |
| `index/en/v1/` | 114 surah indexes — every word id, clip and gloss |
| `tools/` | the pipeline that produced them |
| `examples/` | reference player and Cloudflare Worker |

Audio files are **not** in this repository — 20,498 clips, 128 MB. They are served from
the endpoint above. The indexes are, because they are small and useful on their own.

## Current data

| | |
|---|---|
| Language | English word-by-word |
| Coverage | 77,432 / 77,432 words — 100% |
| Clips | 20,498 distinct |
| Size | 128 MB, Opus 32 kbps mono |
| Alignment | matches QUSX on every ayah of all 114 surahs |
| Layouts | one id space across all ten |

Coverage is 100% of positions, but only a small fraction of clips have been checked by
a person. See [Known limitations](#known-limitations).

## Rebuilding

```bash
python tools/qul_english_to_csv.py colored-english-wbw.zip -o data-en/source/wbw.csv
python tools/make_wordlist.py data-en/source/wbw.csv \
       --wordlist data-en/wordlist.csv --index data-en/index.csv \
       --casefold --strip-brackets
python tools/compare_qusx.py --data data-en          # must report 0 differences
python tools/tts_generate.py --data data-en --voice <id>
python tools/export_words.py --data data-en --out dist-words --base-url <url>
python tools/upload_s3.py --dist dist-words --prefix en --version v1
```

`compare_qusx.py` is not optional. Translation sources segment differently from QUSX,
and a mismatch shifts every word after it inside that verse.

## Notes worth keeping

**Words repeat, so store them once.** 77,432 positions reduce to 20,498 distinct
clips; `Allah` alone covers 3,141 of them. Indexes therefore map *position → clip* and
*clip → text* separately. Per-surah audio packing was tried and abandoned: it
duplicates shared words across files and destroys cache reuse.

**Brackets mean two different things.** Around a *word* — `In (the) name` — they mark
something the translator supplied that is absent from the Arabic; that marking is the
point of a scholarly gloss and is preserved in `text`. Around an *inflection* —
`disbelieve [d]`, `year (s)` — they are part of one word, and folding them to
whitespace produces `disbelieve d`, spoken as "disbelieve dee". Join inflections to
their stem; fold word-brackets only for the audio key, never for the published text.

Folding for the audio key cut the English corpus from 771,618 to 276,928 characters —
a 64% reduction in synthesis cost.

**Segmentation drift is silent.** The English source merged `بَعْدَ مَا` into one gloss
in three places where QUSX splits it. Totals still looked plausible; only a per-ayah
comparison found it.

**Audio is immutable, indexes are not.** Clips are cached for a year, so a correction
must ship under a new version prefix. Indexes are regenerated in place and must
revalidate — a long-cached index silently hides new fields.

## Known limitations

- Roughly 1,400 clips were verified by speech-to-text round-trip; the remaining ~19,000
  were not. Most are multi-word glosses, which tested reliably, but "untested" is the
  honest description.
- Very short function words are the weak point. Generation is non-deterministic and
  roughly one attempt in five of a word like `In` came back wrong; those are retried
  until they transcribe correctly, but the check accepts near-homophones.
- Transliterated names (`Lut`, `Yaqub`, `Zaqqum`, and the letter-openers `Alif`,
  `Laam`, `Meem`) cannot be verified by transcription — the recogniser cannot spell
  them either. 713 glosses, 4,362 positions, unverified by design.
- There is no correction workflow yet. Errors found by readers have nowhere to go.
- The text was reviewed with `tools/review_english.py`; split inflections and lost
  bracket marking were found and fixed. Remaining findings are source oddities
  (a handful of opening quotation marks) rather than processing damage.

## Licence

Code and the spec: MIT — see `LICENSE`.

The English gloss text is not ours. See `DATA-LICENCE.md` before redistributing it or
the audio derived from it.
