# Shorts Automation

Generate a narrated, vertical YouTube Short from a topic and a reference image of a skull character. The script writes a Portuguese script, creates narration and six animated scenes, then assembles a video with a three-second opening hook, sound effects, transitions, and burned-in captions.

## Requirements

- Python 3.10 or newer
- `ffmpeg` and `ffprobe` available on your `PATH` (FFmpeg must support the `libx264` encoder and ASS subtitle filter)
- API keys for Anthropic, xAI, and ElevenLabs

Install the Python packages in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install anthropic requests python-dotenv
```

Create a `.env` file in the project root:

```dotenv
ANTHROPIC_API_KEY=your_anthropic_key
XAI_API_KEY=your_xai_key
ELEVENLABS_API_KEY=your_elevenlabs_key

# Optional overrides:
ELEVENLABS_VOICE_ID=your_voice_id
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
```

The default voice ID is `pNInz6obpgDQGcFmaJgB`. `.env` and generated output are excluded from Git.

## Generate a Short

Run the script from the project root:

```bash
python3 main.py "What happens if you never sleep?"
```

By default, the character reference is [`assets/caveira-de-referencia.png`](assets/caveira-de-referencia.png). Pass a different image as the second argument to use it instead:

```bash
python3 main.py "What happens if you never sleep?" path/to/reference.png
```

The prompts ask for a Portuguese script and narration, even if the topic is written in English. The pipeline:

1. Uses Claude to describe the reference character and write a six-scene script.
2. Uses ElevenLabs to narrate the script.
3. Uses Grok Imagine to create an image and animated clip for each scene.
4. Uses FFmpeg to assemble a 1080 × 1920, 30 fps video with a three-second hook, transitions, audio, and captions.

The final MP4 is saved in `shorts_output/` with a filename based on the generated title. That folder also contains `roteiro.json`, `narracao.mp3`, scene images and clips, prompts, subtitles, and intermediate media.

## Optional assets

Place these files in `assets/` to customize the soundtrack:

| File | Use |
| --- | --- |
| `background.mp3` | Background music; omitted when absent |
| `click.mp3` | Hook cut sound; synthesized when absent |
| `whoosh.mp3` | Scene transition sound; synthesized when absent |

## Add captions to an existing video

If `shorts_output/roteiro.json` already exists, you can burn its scene captions into a video:

```bash
python3 main.py --legendar shorts_output/video.mp4
```

This writes a sibling file ending in `_legendado.mp4`. The caption timings come from the scene durations in `roteiro.json`, so use a video assembled from the matching script.

## Reusing generated files

The script skips narration, scene images, scene videos, and normalized clips when their files already exist in `shorts_output/`. Before generating a different topic or changing the reference image, move or remove that folder to avoid mixing files from different runs. A fresh run makes calls to all three external APIs and can take time while video generation completes.

