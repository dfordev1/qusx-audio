# Data provenance and licence

The MIT licence in `LICENSE` covers **code and the specification only**. The content
below has other owners, and this is deliberately stated rather than assumed.

## English word-by-word text

Source: the *Colored English word-by-word translation* published by the
[Quranic Universal Library](https://qul.tarteel.ai) (resource 92).

QUL does not mark this resource as copyrighted, unlike some others in the same
collection — the German word-by-word set, for example, is flagged as copyrighted.
That is a useful signal but **it is not a licence grant**, and no translator is named
on the resource page.

Before redistributing the gloss text — or audio derived from it — confirm its terms
with QUL. This repository reproduces the text in `index/en/v1/*.json` on the
understanding that QUL publishes it for reuse; if that understanding is wrong, the
files will be removed.

Three glosses were modified from the source, splitting `after what` into `after` +
`what` at 2:181, 8:6 and 13:37 so the segmentation matches QUSX word-for-word. No
other text was altered.

## Audio

Generated speech, not a human recording. Produced with ElevenLabs from a voice clone,
speaking the English gloss text above. The audio is therefore a derivative of that
text and inherits whatever terms apply to it.

Voice model terms are ElevenLabs' and belong to the account that generated the audio.

## Arabic text and word ids

From [QUSX](https://github.com/dfordev1/usxv2). Word ids in these indexes refer to that
project; the Arabic text itself is not reproduced here.

## If you are the rights holder

Open an issue and the material will be removed or attributed as you require.
