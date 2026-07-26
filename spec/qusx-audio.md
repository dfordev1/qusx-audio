# QUSX-Audio 0.1

A companion layer that attaches audio to [QUSX](https://github.com/dfordev1/usxv2)
words without changing QUSX itself.

QUSX describes the text of the Qur'an: words, morphology, and print layout. It
deliberately has no notion of audio. QUSX-Audio adds that as a sidecar, the same way
USX keeps scripture text and audio timings in separate files rather than one.

Nothing here requires a change to a `.qusx.xml` file. A consumer that ignores
QUSX-Audio sees exactly the QUSX it already knows.

## Why a sidecar

Embedding audio attributes inside `<word>` would force every consumer to parse data
most of them do not want, and would tie one text standard to one particular recording.
Keeping them apart means:

- Many audio sets — languages, voices, recitations — can address the same text.
- Audio can be corrected and re-published without touching the text.
- A text-only consumer is unaffected.

## Addressing

A QUSX word id is a single global position that runs across the whole mushaf. It is
sequential and **includes ayah-number tokens**, so ids are not the same as word counts:

```xml
<word id="1" position="1" root="س م و" stem="سْمِ" lemma="اسْم">بِسْمِ</word>
```

QUSX-Audio addresses audio by that id and nothing else. There is no surah/ayah/word
tuple in the format, because the id already encodes position unambiguously.

### Ayah-number tokens have no audio

Elements carrying `type="number"` are verse numerals, not words. They occupy an id but
must be skipped when mapping audio. In the current corpus that is 6,236 of 83,668
elements, leaving 77,432 real words.

### Ids are layout-independent

QUSX publishes ten print layouts (`madani-v2`, `indopak-15`, `nastaleeq`, …). They
differ in script and page/line milestones but share one word-id space — verified across
all ten for surahs 1, 2, 9, 18, 55, 78 and 114: identical counts, identical id
sequences.

An index therefore carries `"layoutAgnostic": true` and needs no layout qualifier. A
producer that cannot make that guarantee must set it to `false` and name its layout.

## File layout

```
<base>/<lang>/<version>/index/<surah:03d>.json     one per surah
<base>/<lang>/<version>/index.json                 manifest
<base>/<lang>/<version>/audio/<clipId>.opus        one per distinct gloss
```

`<version>` is required. Audio is served with a one-year immutable cache, so a
corrected clip **must** ship under a new version. Overwriting a clip in place means no
existing listener ever receives the fix.

## Surah index

```json
{
  "qusxAudio": "0.1",
  "surah": 1,
  "language": "en",
  "layoutAgnostic": true,
  "base": "https://example.workers.dev/en/v1/",
  "words":  { "1": "g63ddb8db00", "2": "gab789dae55" },
  "text":   { "g63ddb8db00": "In (the) name", "gab789dae55": "(of) Allah" },
  "spoken": { "g63ddb8db00": "In the name",   "gab789dae55": "of Allah" }
}
```

| field | meaning |
|---|---|
| `words` | QUSX word id → clip id |
| `text` | clip id → the gloss **as printed**, brackets intact |
| `spoken` | clip id → what the audio actually says, where it differs from `text` |
| `base` | absolute prefix for `audio/<clipId>.opus` |

### text and spoken are not the same string

Word-by-word translations bracket words the translator supplied rather than words
present in the Arabic: `In (the) name` means `the` has no Arabic counterpart. That
distinction is the point of a scholarly gloss and must survive publication.

A bracket cannot be pronounced, so the audio speaks the folded form. Publishing only
the folded form erases the marking from every gloss; publishing only the printed form
misrepresents what the listener hears. Emit both, and `spoken` only where it differs —
in the reference English set that is 11,205 of 20,498 clips.

Consumers should render `text` and treat `spoken` as a description of the audio.

### Why two maps instead of one

Words repeat heavily. In surah 2, 6,117 positions are covered by 2,820 distinct clips;
`Allah` alone accounts for 3,141 positions across the corpus. Keying `words` by
position and `text` by clip stores each string and each file exactly once, and lets a
client cache a common word after its first use.

A single flat map would repeat `"Allah"` and its filename thousands of times.

### Text is not optional

An index without `text` is not useful: a consumer receives sounds it cannot render or
verify. Publish the gloss text alongside the audio it speaks.

## Clip ids

A clip id identifies audio content, not a position. Producers should derive it from the
normalised text so that identical text yields one file. The reference implementation
uses `"g" + sha1(normalised_text)[:10]`, where normalising means NFC, stripping
combining marks, collapsing whitespace, and — for languages where it does not change
pronunciation — folding case and bracket characters.

Any stable scheme works; consumers treat the id as opaque.

## Manifest

```json
{
  "qusxAudio": "0.1",
  "language": "en",
  "kind": "word-gloss",
  "layoutAgnostic": true,
  "bitrate": 32,
  "clips": 20537,
  "bytesAudio": 134012928,
  "surahs": [ { "surah": 1, "words": 29, "uniqueClips": 27 } ]
}
```

`kind` distinguishes audio types sharing the same addressing: `word-gloss` for spoken
translations, `recitation` for Qur'anic Arabic.

## Caching

Producers should serve:

- **audio** — `public, max-age=31536000, immutable`. Contents never change for a given
  id; corrections go to a new version.
- **indexes** — short TTL with revalidation. They are regenerated in place, and an
  index cached under a long TTL will keep hiding new fields until it expires.

Consumers should fetch indexes with revalidation and may cache audio indefinitely.

## Serving requirements

- `Accept-Ranges: bytes` and `206` responses, so players can seek.
- CORS: `Access-Control-Allow-Origin`, and `Content-Length`, `Content-Range`,
  `Accept-Ranges`, `ETag` in `Access-Control-Expose-Headers`.
- `404` as JSON on an unknown key, never an HTML error page.

## Consuming

```js
const idx = await (await fetch(`${BASE}/index/002.json`, {cache:'no-cache'})).json();

// from a QUSX <word id="...">
const clip = idx.words[id];
if (clip) {
  const gloss = idx.text[clip];
  new Audio(`${idx.base}audio/${clip}.opus`).play();
}
```

Skip ids absent from `words` — those are number tokens or unvoiced positions.

## Segmentation

Producers must match QUSX word-for-word. Translation sources segment differently: the
reference English set merged `بَعْدَ مَا` into one gloss where QUSX splits it, three
times, which shifted every subsequent word in those verses. Compare per-ayah counts
against QUSX before publishing; a whole-corpus total that matches can still hide
compensating errors.

`tools/compare_qusx.py` performs this check.

## Bracketed inflections

Sources also bracket *inflections*: `disbelieve [d]`, `darkness [es]`, `year (s)`.
These are one word, not two. Folding such brackets to whitespace — correct for a
bracketed word like `[the]` — yields `disbelieve d`, which is then spoken as
"disbelieve dee". Join the ending to its stem before any other bracket handling, and
only when it is genuinely an ending: gluing `(the)` or `[it]` would produce `inthe`.

`tools/review_english.py` reports these along with other text defects.

## Merged positions

Translations do not always segment as the Arabic does. A word-by-word gloss may cover
two Arabic words in one entry -- the Hindi reference set does this 6,910 times -- which
leaves the first of the pair with no text of its own and therefore no clip.

The meaning is not missing; it is inside the neighbour's gloss. So both positions may
point at the same clip, and the pairing is declared:

```json
"words":  { "87": "g4f1c…", "88": "g4f1c…" },
"merged": { "87": "88" }
```

`merged` maps a word id to the id whose gloss covers it. Both keys resolve to the same
clip. A consumer should treat them as one reading -- highlighting both words together,
for instance -- rather than presenting them as independent audio, because tapping
either plays the same thing.

Producers should look forward first when filling such a gap: in the reference set the
absorbing gloss follows in 95% of cases. Never cross an ayah boundary.

Filling gaps this way took the Hindi set from 91.1% of the mushaf to 100% without
generating a single clip.

## Version history

- **0.1** — initial: word-id addressing, `words`/`text` split, layout-agnostic flag,
  optional `spoken`, `roman` and `merged`.
