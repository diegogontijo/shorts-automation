#!/usr/bin/env python3
"""
YouTube Shorts Pipeline — caveira narradora, estilo TikTok/Shorts viral.

Fluxo:
  1. Claude analisa a imagem da caveira (referência visual detalhada para consistência)
  2. Claude gera roteiro cinematográfico com direção de atuação por cena
  3. ElevenLabs gera a narração
  4. Grok Imagine gera imagem cinematográfica para cada cena e anima essa imagem
  5. ffmpeg monta:
     - Hook de 3s (4 clips chamativos com click entre eles)
     - 6 vídeos de cena com xfade + whoosh nas transições
     - SFX de click/whoosh + música de fundo opcional (assets/background.mp3)
     - Legendas virais queimadas no vídeo

Uso:
  python main.py "O que acontece se você nunca dormir?" [assets/caveira-de-referencia.png]

Variáveis de ambiente (.env):
  ANTHROPIC_API_KEY, XAI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

SFX personalizados (opcional — fallback sintético se ausentes):
  assets/click.mp3    ← som de click entre clips do hook
  assets/whoosh.mp3   ← som de transição entre cenas
"""

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if "--legendar" not in sys.argv:
    import anthropic
    import requests
    from dotenv import load_dotenv
else:
    def load_dotenv():
        return None

load_dotenv()

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
XAI_API_KEY         = os.getenv("XAI_API_KEY")
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

OUTPUT_DIR    = Path("shorts_output")
OUTPUT_DIR.mkdir(exist_ok=True)
ASSETS_DIR    = Path("assets")

DEFAULT_SKULL = ASSETS_DIR / "caveira-de-referencia.png"
BG_MUSIC      = ASSETS_DIR / "background.mp3"

NUM_CENAS     = 6
DURACAO_ALVO  = 55
FPS           = 30
RESOLUCAO_W   = 1080
RESOLUCAO_H   = 1920

HOOK_CLIP_DUR = 0.75
HOOK_N_CLIPS  = 4
HOOK_DUR      = HOOK_CLIP_DUR * HOOK_N_CLIPS  # 3.0s total
TRANS_DUR     = 0.15
XAI_IMAGE_MODEL = "grok-imagine-image"
XAI_VIDEO_MODEL = "grok-imagine-video"
XAI_VIDEO_TIMEOUT = 600
XAI_VIDEO_POLL_INTERVAL = 5



# ─── Utilitários ──────────────────────────────────────────────────────────────

def imagem_para_base64(caminho: str) -> tuple[str, str]:
    caminho = Path(caminho)
    with open(caminho, "rb") as f:
        data = f.read()
    if data[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        media_type = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"
    return base64.b64encode(data).decode("utf-8"), media_type


def get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", str(audio_path)
    ], capture_output=True, text=True, check=True)
    for stream in json.loads(result.stdout).get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    return float(DURACAO_ALVO)


def ts(s: float) -> str:
    return f"{int(s//3600):02d}:{int((s%3600)//60):02d}:{int(s%60):02d},{int((s%1)*1000):03d}"


def sanitize_filename(nome: str) -> str:
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in nome)[:40].strip()


def ffmpeg_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:")


def even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


# ─── SFX sintéticos ───────────────────────────────────────────────────────────

def gerar_sfx_click() -> Path:
    custom = ASSETS_DIR / "click.mp3"
    if custom.exists():
        return custom
    path = OUTPUT_DIR / "sfx_click.mp3"
    if not path.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "aevalsrc=0.9*sin(2*PI*1400*t)*exp(-40*t):s=44100:d=0.08",
            str(path)
        ], check=True, capture_output=True)
    return path


def gerar_sfx_whoosh() -> Path:
    custom = ASSETS_DIR / "whoosh.mp3"
    if custom.exists():
        return custom
    path = OUTPUT_DIR / "sfx_whoosh.mp3"
    if not path.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "aevalsrc=0.45*sin(2*PI*(250+3000*t/0.2)*t)*exp(-10*t):s=44100:d=0.2",
            str(path)
        ], check=True, capture_output=True)
    return path


# ─── Etapa 1: Claude analisa a caveira ────────────────────────────────────────

def analisar_caveira(imagem_path: str) -> str:
    print("\n🔍 [1/5] Claude analisando a caveira...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    img_b64, media_type = imagem_para_base64(imagem_path)

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=700,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": (
                    "Analyze this skull character with extreme precision. "
                    "This description will be used VERBATIM in every AI image generation prompt "
                    "to guarantee that the skull looks IDENTICAL across all scenes. "
                    "Any vagueness will cause visual inconsistency — be ruthlessly specific.\n\n"
                    "Cover ALL of the following:\n"
                    "- SKULL FORM: exact cranium shape and volume, jaw width and angle, "
                    "orbital socket style (deep/shallow, round/angular), teeth (count, gaps, damage), "
                    "forehead curvature, zygomatic arch prominence\n"
                    "- SURFACE & MATERIAL: bone texture (smooth/rough/porous), precise color palette "
                    "(ivory, yellowed, stained, bleached patches — note exactly where), visible cracks "
                    "and their location, surface weathering, patina intensity\n"
                    "- DISTINCTIVE FEATURES: any unique markings, carvings, symbols, structural damage, "
                    "asymmetries, or anomalies that make THIS skull unmistakable\n"
                    "- LIGHTING RESPONSE: how light interacts with this surface — "
                    "specular highlights placement, shadow behavior\n"
                    "- ART DIRECTION: photorealistic or stylized, render quality, level of hyperdetail\n\n"
                    "Reply in English, max 130 words, formatted as a tightly written AI generation prompt. "
                    "Begin with 'Photorealistic skull character:'. "
                    "Do NOT use generic skull descriptions. Capture what makes THIS specific skull unique."
                )}
            ]
        }]
    )
    descricao = message.content[0].text.strip()
    print(f"   ✅ Referência capturada: {descricao[:90]}...")
    (OUTPUT_DIR / "descricao_caveira.txt").write_text(descricao, encoding="utf-8")
    return descricao


# ─── Etapa 2: Gerar Roteiro com Claude ────────────────────────────────────────

EXEMPLO_ESTILO = """EXEMPLO DE ESTRUTURA (ursos):
Título: "O Que Acontece se Você For Criado por Ursos a Vida Toda?"
Gancho: "Dia um. Uma ursa te encontra na floresta."

Cena 1 [Dia um] — Ela fareja você, sopra ar quente na sua face. Se deita, te puxa para o pelo. Você sobrevive a noite.
Cena 2 [Ano um] — Você anda nas costas dela agarrado no pelo. Os filhotes te derrubam todo dia. Você aprende a lutar de volta ou sai machucado.
Cena 3 [Ano três] — Primeira caçada real. Desova do salmão. Você entra na água gelada, golpeia como garras. Seu cérebro humano te dá estratégia.
Cena 4 [Ano seis] — Suas unhas endureceram em garras de tanto escalar. Seus braços são anormalmente fortes.
Cena 5 [Ano 18] — Cabelo selvagem, coberto de cicatrizes. Você marca árvores com arranhões e cheiro.
Cena 6 [Ano 20] — Turistas te filmam com mãos tremendo. Um rosnado, uma investida, eles somem gritando. Em horas, o vídeo tremido viraliza."""


def gerar_roteiro(tema: str, descricao_caveira: str) -> dict:
    print(f"\n📝 [2/5] Gerando roteiro: '{tema}'")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Você é roteirista e diretor cinematográfico de YouTube Shorts virais no estilo "What happens if you...".
Narrador protagonista: caveira com este visual exato — {descricao_caveira}

REGRAS OBRIGATÓRIAS DE ESTILO NARRATIVO:
1. Título é sempre uma pergunta gancho ("O que acontece se você...", "Quantas X para você morrer?", "E se...")
2. Segunda pessoa ("você") — o espectador VIVE a experiência, não assiste
3. Cada cena começa com um marcador de tempo que escala dramaticamente (Dia 1 → Semana 2 → Mês 3 → Ano 5...)
4. Escalada progressiva obrigatória: sintoma leve → consequência visceral → colapso irreversível → fim chocante
5. Frases curtas, cortantes, sensoriais. Zero enrolação. Cada palavra precisa pesar.
6. Última cena: UMA frase final, definitiva, visualmente devastadora. Sem explicação.
7. A caveira REAGE emocionalmente em cada cena — ela atua, expressa, não apenas narra em off.

REGRAS CRÍTICAS DO PROMPT_VIDEO (usado para geração de vídeo por IA):
- Cada prompt_video primeiro dirige uma IMAGEM cinematográfica forte e depois uma ANIMAÇÃO dinâmica dessa imagem
- Sempre chame o personagem de "the exact reference skull character" ou "the reference skull character"; NUNCA escreva apenas "a skull", "photorealistic skull", "generic skull" ou descreva uma nova caveira
- O prompt_video NÃO deve redescrever formato de crânio, dentes, mandíbula, cor do osso, rachaduras ou textura; essas características vêm somente da imagem de referência e da descrição acima
- Especifique uma pose visual clara para a imagem inicial e uma ação física dinâmica para o vídeo: movimento da cabeça, olhos, mandíbula, postura do corpo e reação emocional
- Defina o movimento de câmera: aproximação, travelling, handheld sutil, baixo ângulo, close extremo ou perspectiva lateral
- Descreva a iluminação cinematograficamente: ângulo da fonte (lateral 45°, contraluz, inferior), cor dominante, intensidade de sombra
- O ambiente deve ter profundidade e movimento: névoa, partículas, reflexos, vento, objetos em movimento — não seja genérico
- A caveira deve sempre ter olhos visíveis nas órbitas e preservar as características da referência
- Se houver capacete, traje, fumaça, sangue, sombra, raio-x, energia, fogo, gelo ou poeira, esses elementos devem ficar ao redor/sobre a caveira sem cobrir, deformar ou trocar a identidade da caveira

{EXEMPLO_ESTILO}

Agora crie o roteiro sobre: "{tema}"

Retorne APENAS JSON válido (sem markdown):
{{
  "titulo": "Pergunta gancho chamativa em português",
  "gancho": "Primeira frase impactante, máx 10 palavras",
  "narracao_completa": "Narração corrida em português, ~120 palavras, passa pelos {NUM_CENAS} marcadores de tempo em sequência",
  "cenas": [
    {{
      "numero": 1,
      "marcador_tempo": "Dia 1",
      "duracao_segundos": 6,
      "prompt_video": "Ultra-detailed cinematic image-to-video direction in English. MUST refer to the protagonist as 'the exact reference skull character' and MUST NOT describe or invent a new skull design. Include ALL of: (1) strong initial pose with visible eyes and jaw expression; (2) dynamic motion for video — head movement, eye movement, jaw movement, body action; (3) environment with textures, atmospheric particles, depth layers, and moving elements; (4) energetic camera movement and lighting; (5) dramatic mood. Max 120 words.",
      "texto_legenda": "Trecho desta cena começando com o marcador de tempo"
    }}
  ],
  "hashtags": ["#shorts", "#tiktok", "#viral"]
}}

Gere exatamente {NUM_CENAS} cenas. Durações devem somar {DURACAO_ALVO}s.
"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    roteiro = json.loads(raw)
    print(f"   ✅ '{roteiro['titulo']}'")

    with open(OUTPUT_DIR / "roteiro.json", "w", encoding="utf-8") as f:
        json.dump(roteiro, f, ensure_ascii=False, indent=2)

    return roteiro


# ─── Etapa 3: Narração ElevenLabs ─────────────────────────────────────────────

def gerar_narracao(roteiro: dict) -> Path:
    audio_path = OUTPUT_DIR / "narracao.mp3"
    if audio_path.exists():
        print("\n🎙️  [3/5] Narração já existe, pulando ElevenLabs...")
        return audio_path

    print("\n🎙️  [3/5] Gerando narração com ElevenLabs...")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": roteiro["narracao_completa"],
            "model_id": ELEVENLABS_MODEL_ID,
            "voice_settings": {
                "stability": 0.35,
                "similarity_boost": 0.85,
                "style": 0.7,
                "use_speaker_boost": True
            }
        },
        timeout=180,
    )
    if not response.ok:
        raise RuntimeError(f"ElevenLabs TTS falhou: {response.status_code} {response.text}")
    audio_path.write_bytes(response.content)
    print(f"   ✅ Narração salva: {audio_path}")
    return audio_path


# ─── Etapa 4: Gerar Imagens e Vídeos com Grok Imagine ────────────────────────

def imagem_para_data_uri(imagem_path: str) -> str:
    img_b64, media_type = imagem_para_base64(imagem_path)
    return f"data:{media_type};base64,{img_b64}"


def montar_prompt_imagem_cena(cena: dict, descricao_caveira: str) -> str:
    direcao_cena = cena.get("prompt_video") or cena.get("prompt_imagem", "")
    return (
        f"Create a vertical 9:16 cinematic scene image. "
        f"IDENTITY LOCK: <IMAGE_1> is the mandatory visual identity source for the protagonist. "
        f"The image must feature the exact same skull character from <IMAGE_1>, not a new skull. "
        f"If the scene direction conflicts with <IMAGE_1>, obey <IMAGE_1> and preserve identity first. "
        f"REFERENCE SKULL IDENTITY: {descricao_caveira} "
        f"NON-NEGOTIABLE CHARACTER RULES: Keep the original cranium silhouette, jaw width and angle, "
        f"teeth layout and gaps, orbital socket shape, bone color, cracks, texture, proportions, "
        f"asymmetries, weathering, and every distinctive mark from the reference image. "
        f"Do not redesign, replace, stylize away, simplify, beautify, age, damage, melt, fracture, "
        f"mutate, randomize, or morph the skull identity. Do not turn it into a generic skull. "
        f"The skull must always have visible eyes inside the sockets in every frame; the eyes may "
        f"glow or express emotion, but the sockets must never be empty or hidden. "
        f"Allowed scene additions: costumes, props, smoke, fire, ice, blood, armor, helmets, "
        f"astronaut suits, x-ray overlays, cosmic particles, dirt, rain, and dramatic lighting. "
        f"These additions must sit around or on the character without covering, deforming, "
        f"or replacing the identifiable skull features. "
        f"SCENE IMAGE USING THE SAME REFERENCE SKULL CHARACTER: {direcao_cena} "
        f"Make the image visually exciting and ready for animation: strong readable pose, "
        f"clear face, visible eyes, dramatic depth layers, energetic composition, cinematic lighting, "
        f"and environmental elements that can later move dynamically. "
        f"No text, no subtitles, "
        f"no watermarks, no borders, no UI elements. "
        f"FORMAT: Vertical 9:16, ultra-photorealistic, cinema-grade, high detail."
    )


def montar_prompt_animacao(cena: dict) -> str:
    direcao_cena = cena.get("prompt_video") or cena.get("prompt_imagem", "")
    return (
        f"Animate the provided scene image as a dynamic, attractive cinematic vertical video. "
        f"Preserve the exact composition and the exact skull character identity from the image: "
        f"same skull shape, jaw, teeth, eye sockets, visible eyes, cracks, bone texture, color, "
        f"proportions, costume, props, and all distinctive marks. Do not redesign the skull. "
        f"This must not look static: add expressive eye movement, subtle jaw movement, head motion, "
        f"body motion, parallax, foreground/background depth, floating particles, moving light, "
        f"environmental motion, and energetic camera movement that fits the scene. "
        f"Use the scene direction only to guide motion, not to replace the image: {direcao_cena} "
        f"Make the movement dramatic but coherent, with no abrupt cuts, no morphing, no new character, "
        f"no text, no subtitles, no watermarks, no borders, no UI elements."
    )


def duracao_video_cena(cena: dict) -> int:
    return max(1, min(10, int(round(cena["duracao_segundos"]))))


def post_xai_json(url: str, payload: dict, timeout: int = 60) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {XAI_API_KEY}",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if not response.ok:
        raise RuntimeError(f"xAI request failed: {response.status_code} {response.text}")
    return response.json()


def salvar_imagem_xai(data: dict, caminho: Path) -> None:
    item = data.get("data", [{}])[0]
    if item.get("b64_json"):
        caminho.write_bytes(base64.b64decode(item["b64_json"]))
        return
    if item.get("url"):
        response = requests.get(item["url"], timeout=180)
        if not response.ok:
            raise RuntimeError(f"Download da imagem falhou: {response.status_code} {response.text}")
        caminho.write_bytes(response.content)
        return
    raise RuntimeError(f"xAI image response without b64_json or url: {data}")


def gerar_imagem_cena_grok(cena: dict, descricao_caveira: str,
                           imagem_ref_data_uri: str, indice: int) -> Path:
    caminho = OUTPUT_DIR / f"imagem_cena_{indice+1:02d}.png"
    prompt_path = OUTPUT_DIR / f"prompt_imagem_cena_{indice+1:02d}.txt"
    if caminho.exists():
        print(f"   ♻️  Imagem da cena {indice+1} já existe, pulando...")
        return caminho

    print(f"   🖼️  Gerando imagem {indice+1}/{NUM_CENAS}...")
    prompt_final = montar_prompt_imagem_cena(cena, descricao_caveira)
    prompt_path.write_text(prompt_final, encoding="utf-8")

    for tentativa in range(3):
        try:
            data = post_xai_json(
                "https://api.x.ai/v1/images/edits",
                {
                    "model": XAI_IMAGE_MODEL,
                    "prompt": prompt_final,
                    "image": {"type": "image_url", "url": imagem_ref_data_uri},
                    "aspect_ratio": "9:16",
                    "response_format": "b64_json",
                },
            )
            salvar_imagem_xai(data, caminho)
            print(f"   ✅ Imagem {indice+1} salva")
            return caminho
        except Exception as e:
            if tentativa == 2:
                raise
            print(f"   ⚠️  Tentativa {tentativa+1} falhou ({e}). Aguardando 5s...")
            time.sleep(5)


def iniciar_geracao_video(prompt: str, imagem_cena_data_uri: str, duracao: int) -> str:
    data = post_xai_json(
        "https://api.x.ai/v1/videos/generations",
        {
            "model": XAI_VIDEO_MODEL,
            "prompt": prompt,
            "image": {"url": imagem_cena_data_uri},
            "duration": duracao,
            "aspect_ratio": "9:16",
            # "resolution": "720p",
            "resolution": "480p",
        },
    )
    if "request_id" not in data:
        raise RuntimeError(f"xAI video start returned no request_id: {data}")
    return data["request_id"]


def aguardar_video_grok(request_id: str) -> str:
    headers = {"Authorization": f"Bearer {XAI_API_KEY}"}
    deadline = time.time() + XAI_VIDEO_TIMEOUT
    while time.time() < deadline:
        response = requests.get(
            f"https://api.x.ai/v1/videos/{request_id}",
            headers=headers,
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"xAI video poll failed: {response.status_code} {response.text}")
        data = response.json()
        status = data.get("status")
        if status == "done":
            video = data.get("video") or {}
            url = video.get("url")
            if not url:
                raise RuntimeError(f"xAI video done without URL: {data}")
            return url
        if status in {"failed", "expired"}:
            raise RuntimeError(f"xAI video generation {status}: {data}")
        print(f"      Status Grok: {status or 'pending'}; aguardando...")
        time.sleep(XAI_VIDEO_POLL_INTERVAL)
    raise TimeoutError(f"xAI video generation timed out after {XAI_VIDEO_TIMEOUT}s: {request_id}")


def baixar_video(url: str, destino: Path) -> None:
    response = requests.get(url, timeout=180)
    if not response.ok:
        raise RuntimeError(f"Download do vídeo falhou: {response.status_code} {response.text}")
    destino.write_bytes(response.content)


def gerar_video_grok(cena: dict, imagem_cena_path: Path, indice: int) -> Path:
    caminho = OUTPUT_DIR / f"video_cena_{indice+1:02d}.mp4"
    prompt_path = OUTPUT_DIR / f"prompt_video_cena_{indice+1:02d}.txt"
    if caminho.exists():
        print(f"   ♻️  Vídeo da cena {indice+1} já existe, pulando...")
        return caminho

    duracao = duracao_video_cena(cena)
    print(f"   🎬 Animando imagem da cena {indice+1}/{NUM_CENAS} ({duracao}s)...")
    prompt_final = montar_prompt_animacao(cena)
    prompt_path.write_text(prompt_final, encoding="utf-8")
    imagem_cena_data_uri = imagem_para_data_uri(str(imagem_cena_path))

    for tentativa in range(3):
        try:
            request_id = iniciar_geracao_video(prompt_final, imagem_cena_data_uri, duracao)
            print(f"      Request xAI: {request_id}")
            video_url = aguardar_video_grok(request_id)
            baixar_video(video_url, caminho)
            print(f"   ✅ Vídeo {indice+1} salvo")
            return caminho
        except Exception as e:
            if tentativa == 2:
                raise
            print(f"   ⚠️  Tentativa {tentativa+1} falhou ({e}). Aguardando 5s...")
            time.sleep(5)


def gerar_todos_videos(roteiro: dict, descricao_caveira: str,
                       imagem_path: str) -> list[Path]:
    print("\n🎬 [3/5] Gerando imagens e animando com Grok Imagine...")
    imagem_ref_data_uri = imagem_para_data_uri(imagem_path)
    videos = []
    for i, cena in enumerate(roteiro["cenas"]):
        imagem_cena = gerar_imagem_cena_grok(cena, descricao_caveira, imagem_ref_data_uri, i)
        videos.append(gerar_video_grok(cena, imagem_cena, i))
        time.sleep(1)
    return videos


# ─── Etapa 5: Legendas ────────────────────────────────────────────────────────

def ass_escape(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def ass_time(s: float) -> str:
    cs = int(round((s % 1) * 100))
    return f"{int(s//3600)}:{int((s%3600)//60):02d}:{int(s%60):02d}.{cs:02d}"


def quebrar_legenda(texto: str, max_chars: int = 34) -> list[str]:
    palavras = texto.split()
    linhas = []
    atual = ""
    for palavra in palavras:
        candidato = f"{atual} {palavra}".strip()
        if len(candidato) <= max_chars:
            atual = candidato
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def gerar_legendas(roteiro: dict) -> Path:
    print("   Gerando legendas ASS...")
    path = OUTPUT_DIR / "legendas.ass"
    duracoes = [duracao_video_cena(c) for c in roteiro["cenas"]]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {RESOLUCAO_W}
PlayResY: {RESOLUCAO_H}
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Shorts,Arial,74,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,5,1,2,80,80,235,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    eventos = []
    inicio_cena = HOOK_DUR
    for cena, dur in zip(roteiro["cenas"], duracoes):
        texto = cena.get("texto_legenda") or cena.get("marcador_tempo", "")
        frases = [f.strip() for f in texto.replace("!", ".").replace("?", ".").split(".") if f.strip()]
        if not frases:
            frases = [texto.strip()]

        tempo_por_frase = max(1.25, dur / len(frases))
        t = inicio_cena
        for frase in frases:
            fim = min(inicio_cena + dur, t + tempo_por_frase)
            linhas = quebrar_legenda(frase.upper())
            for bloco in [linhas[i:i+2] for i in range(0, len(linhas), 2)]:
                texto_ass = ass_escape("\\N".join(bloco))
                eventos.append(
                    f"Dialogue: 0,{ass_time(t)},{ass_time(fim)},Shorts,,0,0,0,,{texto_ass}"
                )
            t = fim
        inicio_cena += dur - TRANS_DUR

    path.write_text(header + "\n".join(eventos) + "\n", encoding="utf-8")
    return path


def queimar_legendas(video_path: Path, legendas_path: Path,
                     destino: Path | None = None) -> Path:
    destino = destino or video_path.with_name(f"{video_path.stem}_legendado{video_path.suffix}")
    filtro = f"ass='{ffmpeg_path(legendas_path)}'"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", filtro,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        str(destino)
    ], check=True, capture_output=True)
    return destino


# ─── Montagem Final ────────────────────────────────────────────────────────────

def normalizar_clipes_grok(videos: list[Path],
                           duracoes_reais: list[float]) -> list[Path]:
    clipes = []
    for i, video in enumerate(videos):
        clipe    = OUTPUT_DIR / f"clipe_animado_{i+1:02d}.mp4"
        dur      = duracoes_reais[i]
        vf = (
            f"scale={RESOLUCAO_W}:{RESOLUCAO_H}:force_original_aspect_ratio=increase,"
            f"crop={RESOLUCAO_W}:{RESOLUCAO_H},fps={FPS},setsar=1"
        )
        if not clipe.exists():
            subprocess.run([
                "ffmpeg", "-y", "-i", str(video),
                "-vf", vf, "-t", str(dur),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-an",
                "-pix_fmt", "yuv420p", str(clipe)
            ], check=True, capture_output=True)
            print(f"   ✅ Clipe {i+1}/{len(videos)} normalizado")
        else:
            print(f"   ♻️  Clipe {i+1} já existe")
        clipes.append(clipe)
    return clipes


def criar_hook_video(clipes: list[Path], duracoes_reais: list[float]) -> Path:
    """4 clips de 0.75s do meio das cenas 1-4, com zoom punch sutil."""
    hook_clips = []
    w_punch = even(int(RESOLUCAO_W * 1.08))
    h_punch = even(int(RESOLUCAO_H * 1.08))

    for j, idx in enumerate([1, 2, 3, 4]):
        start = duracoes_reais[idx] / 2
        dest  = OUTPUT_DIR / f"hook_{j+1:02d}.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}", "-t", f"{HOOK_CLIP_DUR:.3f}",
            "-i", str(clipes[idx]),
            "-vf", f"scale={w_punch}:{h_punch},crop={RESOLUCAO_W}:{RESOLUCAO_H}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(dest)
        ], check=True, capture_output=True)
        hook_clips.append(dest)

    lista = OUTPUT_DIR / "hook_lista.txt"
    lista.write_text("\n".join(f"file '{c.absolute()}'" for c in hook_clips))

    hook_video = OUTPUT_DIR / "hook_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lista), "-c", "copy", str(hook_video)
    ], check=True, capture_output=True)
    return hook_video


def criar_hook_audio(click_sfx: Path) -> Path:
    """3s de áudio com click a cada 0.75s (nos cortes entre clips)."""
    silencio = OUTPUT_DIR / "hook_silencio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc=0:s=44100:d={HOOK_DUR}",
        str(silencio)
    ], check=True, capture_output=True)

    n_clicks     = HOOK_N_CLIPS - 1
    inputs       = ["-i", str(silencio)]
    filter_parts = ["[0:a]acopy[base]"]
    mix_labels   = ["[base]"]

    for i in range(n_clicks):
        delay_ms  = int((i + 1) * HOOK_CLIP_DUR * 1000)
        inputs   += ["-i", str(click_sfx)]
        filter_parts.append(f"[{i+1}:a]adelay={delay_ms}|{delay_ms}[c{i}]")
        mix_labels.append(f"[c{i}]")

    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0[aout]"
    )

    hook_audio = OUTPUT_DIR / "hook_audio.mp3"
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]", str(hook_audio)
    ], check=True, capture_output=True)
    return hook_audio


def concatenar_com_xfade(clipes: list[Path], duracoes: list[float]) -> Path:
    """Concatena clipes com crossfade de TRANS_DUR segundos entre cada um."""
    inputs = []
    for c in clipes:
        inputs += ["-i", str(c)]

    filter_parts = []
    prev       = "[0:v]"
    cumulative = 0.0

    for i in range(1, len(clipes)):
        cumulative += duracoes[i - 1]
        offset    = cumulative - i * TRANS_DUR
        out_label = "[vout]" if i == len(clipes) - 1 else f"[xf{i}]"
        filter_parts.append(
            f"{prev}[{i}:v]xfade=transition=fade:"
            f"duration={TRANS_DUR:.3f}:offset={offset:.3f}{out_label}"
        )
        prev = out_label

    video_trans = OUTPUT_DIR / "video_transicoes.mp4"
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(video_trans)
    ], check=True, capture_output=True)
    return video_trans


def criar_audio_edicao(duracoes: list[float], total_dur: float,
                       narracao: Path | None = None) -> Path:
    """Cria trilha final com narração, clicks, whooshes e música opcional."""
    click_sfx = gerar_sfx_click()
    whoosh_sfx = gerar_sfx_whoosh()

    inputs = ["-f", "lavfi", "-i", f"aevalsrc=0:s=44100:d={total_dur:.3f}"]
    filter_parts = ["[0:a]acopy[base]"]
    mix_labels = ["[base]"]
    input_idx = 1

    if narracao:
        inputs += ["-i", str(narracao)]
        filter_parts.append(
            f"[{input_idx}:a]volume=1.0,adelay={int(HOOK_DUR * 1000)}|"
            f"{int(HOOK_DUR * 1000)}[narr]"
        )
        mix_labels.append("[narr]")
        input_idx += 1

    for i in range(HOOK_N_CLIPS - 1):
        delay_ms = int((i + 1) * HOOK_CLIP_DUR * 1000)
        inputs += ["-i", str(click_sfx)]
        filter_parts.append(
            f"[{input_idx}:a]volume=0.95,adelay={delay_ms}|{delay_ms}[click{i}]"
        )
        mix_labels.append(f"[click{i}]")
        input_idx += 1

    cumulative = 0.0
    for i, dur in enumerate(duracoes[:-1], start=1):
        cumulative += dur
        transition_start = HOOK_DUR + cumulative - i * TRANS_DUR
        delay_ms = max(0, int(transition_start * 1000))
        inputs += ["-i", str(whoosh_sfx)]
        filter_parts.append(
            f"[{input_idx}:a]volume=0.45,adelay={delay_ms}|{delay_ms}[whoosh{i}]"
        )
        mix_labels.append(f"[whoosh{i}]")
        input_idx += 1

    if BG_MUSIC.exists():
        inputs += ["-i", str(BG_MUSIC)]
        filter_parts.append(
            f"[{input_idx}:a]volume=0.08,aloop=loop=-1:size=2e+09,"
            f"atrim=0:{total_dur:.3f}[bg]"
        )
        mix_labels.append("[bg]")

    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:"
        f"duration=first:normalize=0[aout]"
    )

    audio_final = OUTPUT_DIR / "audio_edicao.m4a"
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{total_dur:.3f}",
        str(audio_final)
    ], check=True, capture_output=True)
    return audio_final


def montar_video(clipes_grok: list[Path], roteiro: dict,
                 narracao: Path | None = None) -> Path:
    print("\n🎞️  [5/5] Montando vídeo final...")

    duracoes_reais = [duracao_video_cena(c) for c in roteiro["cenas"]]

    clipes = normalizar_clipes_grok(clipes_grok, duracoes_reais)

    print("   🎣 Criando hook intro (3s)...")
    hook_video = criar_hook_video(clipes, duracoes_reais)

    print("   🔀 Aplicando transições xfade...")
    video_trans = concatenar_com_xfade(clipes, duracoes_reais)

    lista_v = OUTPUT_DIR / "lista_video_full.txt"
    lista_v.write_text(
        f"file '{hook_video.absolute()}'\nfile '{video_trans.absolute()}'"
    )
    video_completo = OUTPUT_DIR / "video_completo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lista_v), "-c", "copy", str(video_completo)
    ], check=True, capture_output=True)

    total_dur = HOOK_DUR + sum(duracoes_reais) - (NUM_CENAS - 1) * TRANS_DUR
    saida = OUTPUT_DIR / f"{sanitize_filename(roteiro['titulo'])}.mp4"

    print("   🎧 Criando trilha de edição...")
    audio_edicao = criar_audio_edicao(duracoes_reais, total_dur, narracao)

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_completo),
        "-i", str(audio_edicao),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-t", f"{total_dur:.3f}", str(saida)
    ], check=True, capture_output=True)
    print("   ✅ Clicks, whooshes e música de fundo mixados")

    legendas = gerar_legendas(roteiro)
    video_sem_legenda = saida.with_name(f"{saida.stem}_sem_legenda{saida.suffix}")
    if video_sem_legenda.exists():
        video_sem_legenda.unlink()
    saida.replace(video_sem_legenda)
    print("   Queimando legendas no vídeo final...")
    queimar_legendas(video_sem_legenda, legendas, saida)

    print(f"\n✅ Vídeo final: {saida}")
    return saida


# ─── Pipeline Principal ───────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Uso: python main.py \"Tema\" [caminho/caveira.png]")
        print("Ou:  python main.py --legendar shorts_output/video.mp4")
        print('Exemplo: python main.py "O que acontece se você nunca dormir?"')
        sys.exit(1)

    if sys.argv[1] == "--legendar":
        if len(sys.argv) < 3:
            print("Uso: python main.py --legendar shorts_output/video.mp4")
            sys.exit(1)
        video_path = Path(sys.argv[2])
        roteiro_path = OUTPUT_DIR / "roteiro.json"
        if not video_path.exists():
            print(f"❌ Vídeo não encontrado: {video_path}")
            sys.exit(1)
        if not roteiro_path.exists():
            print(f"❌ Roteiro não encontrado: {roteiro_path}")
            sys.exit(1)
        roteiro = json.loads(roteiro_path.read_text(encoding="utf-8"))
        legendas = gerar_legendas(roteiro)
        saida = queimar_legendas(video_path, legendas)
        print(f"Video legendado: {saida}")
        return

    tema        = sys.argv[1]
    imagem_path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_SKULL)

    if not Path(imagem_path).exists():
        print(f"❌ Imagem não encontrada: {imagem_path}")
        sys.exit(1)

    for var, nome in [
        (ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY"),
        (XAI_API_KEY,       "XAI_API_KEY"),
        (ELEVENLABS_API_KEY, "ELEVENLABS_API_KEY"),
    ]:
        if not var:
            print(f"❌ {nome} não definida no .env")
            sys.exit(1)

    print(f"\n🚀 Tema: \"{tema}\"")
    print(f"💀 Caveira: {imagem_path}")
    if BG_MUSIC.exists():
        print(f"🎵 Música: {BG_MUSIC}")

    inicio = time.time()

    desc_caveira = analisar_caveira(imagem_path)
    roteiro      = gerar_roteiro(tema, desc_caveira)
    audio        = gerar_narracao(roteiro)
    videos       = gerar_todos_videos(roteiro, desc_caveira, imagem_path)
    video        = montar_video(videos, roteiro, audio)

    print(f"\n🎉 Pronto em {(time.time()-inicio)/60:.1f} minutos!")
    print(f"📁 {video}")
    print(f"🏷️  {' '.join(roteiro['hashtags'])}")


if __name__ == "__main__":
    main()
