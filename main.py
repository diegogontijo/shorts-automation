#!/usr/bin/env python3
"""
YouTube Shorts Pipeline — caveira narradora, estilo TikTok/Shorts viral.

Fluxo:
  1. Claude analisa a imagem da caveira (referência visual detalhada para consistência)
  2. Claude gera roteiro cinematográfico com direção de atuação por cena
  3. ElevenLabs gera a narração (desabilitado por ora)
  4. Grok (xAI Aurora) gera imagem cinematográfica para cada cena
  5. ffmpeg monta:
     - Hook de 3s (4 clips chamativos com click entre eles)
     - 6 cenas com Ken Burns + xfade + whoosh nas transições
     - Legendas virais (desabilitado por ora)
     - Música de fundo opcional (assets/background.mp3)

Uso:
  python main.py "O que acontece se você nunca dormir?" [assets/caveira-de-referencia.png]

Variáveis de ambiente (.env):
  ANTHROPIC_API_KEY, XAI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

SFX personalizados (opcional — fallback sintético se ausentes):
  assets/click.mp3    ← som de click entre clips do hook
  assets/whoosh.mp3   ← som de transição entre cenas
"""

import anthropic
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from openai import OpenAI
import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
XAI_API_KEY         = os.getenv("XAI_API_KEY")
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")

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

xai_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")


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

REGRAS CRÍTICAS DO PROMPT_IMAGEM (usado para geração de imagem por IA):
- Cada prompt_imagem dirige uma POSE cinematográfica real da caveira — não uma cena genérica
- Especifique a AÇÃO física da caveira: ângulo exato da cabeça, tensão na mandíbula, postura do corpo
- Defina o PONTO DE VISTA da câmera: baixo ângulo / close extremo nos olhos / plongée / perspectiva lateral
- Descreva a ILUMINAÇÃO cinematograficamente: ângulo da fonte (lateral 45°, contraluz, inferior), cor dominante, intensidade de sombra
- O AMBIENTE deve ter profundidade e atmosfera: névoa, partículas, reflexos, elementos específicos — não seja genérico
- A caveira deve parecer que está no meio de uma REAÇÃO, não posando

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
      "duracao_segundos": 9,
      "prompt_imagem": "Ultra-detailed cinematic image direction in English. MUST include ALL of: (1) skull character's precise physical pose — exact head angle, jaw state, body posture conveying emotion; (2) environment with specific textures, atmospheric particles, depth layers; (3) camera angle and framing; (4) lighting — color temperature, direction, shadow intensity; (5) overall dramatic mood. The skull must look mid-action, not static. Max 90 words.",
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
# DESABILITADO — narração e legendas puladas por enquanto

# def gerar_narracao(roteiro: dict) -> Path:
#     print("\n🎙️  [3/5] Gerando narração com ElevenLabs...")
#     response = requests.post(
#         f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
#         headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
#         json={
#             "text": roteiro["narracao_completa"],
#             "model_id": "eleven_multilingual_v2",
#             "voice_settings": {
#                 "stability": 0.35,
#                 "similarity_boost": 0.85,
#                 "style": 0.7,
#                 "use_speaker_boost": True
#             }
#         }
#     )
#     response.raise_for_status()
#     audio_path = OUTPUT_DIR / "narracao.mp3"
#     audio_path.write_bytes(response.content)
#     print(f"   ✅ Narração salva")
#     return audio_path


# ─── Etapa 4: Gerar Imagens com Grok (xAI Aurora) ────────────────────────────

def gerar_imagem_grok(cena: dict, descricao_caveira: str, indice: int) -> Path:
    caminho = OUTPUT_DIR / f"cena_{indice+1:02d}.png"
    if caminho.exists():
        print(f"   ♻️  Cena {indice+1} já existe, pulando...")
        return caminho

    print(f"   🎨 Gerando imagem {indice+1}/{NUM_CENAS}...")

    prompt_final = (
        # Ancoragem de personagem — primeiro e mais enfático
        f"SKULL CHARACTER — PRESERVE EXACT APPEARANCE: {descricao_caveira} "
        f"CRITICAL: Do NOT alter the skull's shape, proportions, color, texture, cracks, "
        f"or any distinctive feature. This skull must be unmistakably identical to the reference. "
        # Direção de cena
        f"SCENE: {cena['prompt_imagem']} "
        # Direção de atuação / pose
        f"PERFORMANCE: The skull is caught mid-action — expressive head tilt conveying tension or dread, "
        f"jaw subtly open or clenched, body posture leaning into the scene's emotional weight. "
        f"It must look like a character reacting, not an object sitting. "
        # Cinematografia
        f"CINEMATOGRAPHY: Ultra-photorealistic, hyperdetailed, cinema-grade quality. "
        f"Physically-based bone rendering with accurate surface properties. "
        f"Dramatic chiaroscuro lighting — deep shadows carving the skull's geometry, "
        f"high-contrast highlights on bone ridges and orbital rims. "
        f"Shallow depth of field: skull in razor-sharp focus, background in cinematic bokeh. "
        f"Color grading: desaturated, cold or sickly color cast matching the scene's dread. "
        # Composição
        f"COMPOSITION: Vertical 9:16 framing. Skull dominates center-to-upper frame. "
        f"Atmospheric environment fills the background with depth and texture. "
        f"No text, no watermarks, no borders, no UI elements."
    )

    for tentativa in range(3):
        try:
            resp = xai_client.images.generate(
                model="grok-2-image-1212",
                prompt=prompt_final,
                n=1,
                response_format="b64_json"
            )
            caminho.write_bytes(base64.b64decode(resp.data[0].b64_json))
            print(f"   ✅ Imagem {indice+1} salva")
            return caminho
        except Exception as e:
            if tentativa == 2:
                raise
            print(f"   ⚠️  Tentativa {tentativa+1} falhou ({e}). Aguardando 5s...")
            time.sleep(5)


def gerar_todas_imagens(roteiro: dict, descricao_caveira: str,
                        imagem_path: str) -> list[Path]:
    print("\n🎨 [3/5] Gerando imagens com Grok (xAI Aurora)...")
    imagens = []
    for i, cena in enumerate(roteiro["cenas"]):
        imagens.append(gerar_imagem_grok(cena, descricao_caveira, i))
        time.sleep(1)
    return imagens


# ─── Etapa 5: Legendas ────────────────────────────────────────────────────────
# DESABILITADO

# def gerar_legendas(roteiro: dict, audio_path: Path) -> Path: ...


# ─── Montagem Final ────────────────────────────────────────────────────────────

def renderizar_clipes_ken_burns(imagens: list[Path],
                                 duracoes_reais: list[float]) -> list[Path]:
    clipes = []
    for i, img in enumerate(imagens):
        clipe    = OUTPUT_DIR / f"clipe_{i+1:02d}.mp4"
        dur      = duracoes_reais[i]
        zoom_dir = 1 if i % 2 == 0 else -1
        zoom_expr = (
            f"zoompan=z='if(lte(zoom,1.0),1.05,max(1.001,zoom+{zoom_dir}*0.0008))':"
            f"d={int(dur * FPS)}:s={RESOLUCAO_W}x{RESOLUCAO_H}:fps={FPS}"
        )
        if not clipe.exists():
            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", str(img),
                "-vf", zoom_expr, "-t", str(dur),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p", str(clipe)
            ], check=True, capture_output=True)
            print(f"   ✅ Clipe {i+1}/{len(imagens)} renderizado")
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


def mixar_whoosh_na_narracao(narracao: Path, whoosh_sfx: Path,
                              duracoes: list[float]) -> Path:
    """Adiciona whoosh sutil (35% volume) em cada ponto de transição."""
    inputs       = ["-i", str(narracao)]
    filter_parts = ["[0:a]acopy[narr]"]
    mix_labels   = ["[narr]"]

    cumulative = 0.0
    for i, dur in enumerate(duracoes[:-1]):
        cumulative += dur - TRANS_DUR
        delay_ms    = int(cumulative * 1000)
        n = i + 1
        inputs      += ["-i", str(whoosh_sfx)]
        filter_parts.append(
            f"[{n}:a]volume=0.35,adelay={delay_ms}|{delay_ms}[w{i}]"
        )
        mix_labels.append(f"[w{i}]")
        cumulative  += TRANS_DUR

    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0[aout]"
    )

    audio_final = OUTPUT_DIR / "narracao_com_whoosh.mp3"
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]", str(audio_final)
    ], check=True, capture_output=True)
    return audio_final


def montar_video(imagens: list[Path], roteiro: dict) -> Path:
    print("\n🎞️  [5/5] Montando vídeo final...")

    duracoes_reais = [c["duracao_segundos"] for c in roteiro["cenas"]]

    clipes = renderizar_clipes_ken_burns(imagens, duracoes_reais)

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

    total_dur = HOOK_DUR + DURACAO_ALVO - (NUM_CENAS - 1) * TRANS_DUR
    saida = OUTPUT_DIR / f"{sanitize_filename(roteiro['titulo'])}.mp4"

    if BG_MUSIC.exists():
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_completo),
            "-i", str(BG_MUSIC),
            "-filter_complex",
            "[1:a]volume=0.15,aloop=loop=-1:size=2e+09[bg]",
            "-map", "0:v", "-map", "[bg]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_dur), str(saida)
        ], check=True, capture_output=True)
        print("   🎵 Música de fundo mixada")
    else:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_completo),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-an",
            "-t", str(total_dur), str(saida)
        ], check=True, capture_output=True)

    print(f"\n✅ Vídeo final: {saida}")
    return saida


# ─── Pipeline Principal ───────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Uso: python main.py \"Tema\" [caminho/caveira.png]")
        print('Exemplo: python main.py "O que acontece se você nunca dormir?"')
        sys.exit(1)

    tema        = sys.argv[1]
    imagem_path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_SKULL)

    if not Path(imagem_path).exists():
        print(f"❌ Imagem não encontrada: {imagem_path}")
        sys.exit(1)

    for var, nome in [
        (ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY"),
        (XAI_API_KEY,       "XAI_API_KEY"),
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
    # audio      = gerar_narracao(roteiro)        # DESABILITADO
    imagens      = gerar_todas_imagens(roteiro, desc_caveira, imagem_path)
    # legendas   = gerar_legendas(roteiro, audio)  # DESABILITADO
    video        = montar_video(imagens, roteiro)

    print(f"\n🎉 Pronto em {(time.time()-inicio)/60:.1f} minutos!")
    print(f"📁 {video}")
    print(f"🏷️  {' '.join(roteiro['hashtags'])}")


if __name__ == "__main__":
    main()
