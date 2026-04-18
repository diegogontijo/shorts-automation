#!/usr/bin/env python3
"""
YouTube Shorts Pipeline — caveira narradora, estilo TikTok/Shorts viral.

Fluxo:
  1. Claude analisa a imagem da caveira (referência visual)
  2. Claude gera roteiro estilo "What happens if you..." com time markers
  3. ElevenLabs gera a narração
  4. Grok (Aurora) gera imagem para cada cena
  5. ffmpeg monta:
     - Hook de 3s (4 clips chamativos com click entre eles)
     - 6 cenas com xfade + whoosh nas transições
     - Legendas virais (3 palavras, offset pelo hook)
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

HOOK_CLIP_DUR = 0.75                          # duração de cada clip no hook
HOOK_N_CLIPS  = 4                             # quantos clips no hook
HOOK_DUR      = HOOK_CLIP_DUR * HOOK_N_CLIPS  # 3.0s total
TRANS_DUR     = 0.15                          # duração do xfade entre cenas

xai_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")


# ─── Utilitários ──────────────────────────────────────────────────────────────

def imagem_para_base64(caminho: str) -> tuple[str, str]:
    caminho = Path(caminho)
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".png": "image/png", ".webp": "image/webp"}
    media_type = media_types.get(caminho.suffix.lower(), "image/png")
    with open(caminho, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), media_type


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
    """Converte path para formato seguro no ffmpeg (Windows)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def even(n: int) -> int:
    """Garante número par (exigência do libx264)."""
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
        # Frequency sweep ascendente = sensação de whoosh
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "aevalsrc=0.45*sin(2*PI*(250+3000*t/0.2)*t)*exp(-10*t):s=44100:d=0.2",
            str(path)
        ], check=True, capture_output=True)
    return path


# ─── Etapa 1: Claude analisa a caveira ────────────────────────────────────────

def analisar_caveira(imagem_path: str) -> str:
    print("\n🔍 [1/6] Claude analisando a caveira...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    img_b64, media_type = imagem_para_base64(imagem_path)

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": (
                    "Describe this skull/character in detail for consistent visual reference in AI image generation. "
                    "Include: art style, dominant colors, distinctive features, lighting, texture, unique details. "
                    "Reply in English, concisely (max 80 words), formatted as an image generation prompt."
                )}
            ]
        }]
    )
    descricao = message.content[0].text.strip()
    print(f"   ✅ {descricao[:80]}...")
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
    print(f"\n📝 [2/6] Gerando roteiro: '{tema}'")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Você é roteirista de TikToks e YouTube Shorts virais no estilo "What happens if you...".
Narrador: caveira com este visual — {descricao_caveira}

REGRAS OBRIGATÓRIAS DE ESTILO:
1. Título é sempre uma pergunta gancho ("O que acontece se você...", "Quantas X para você morrer?", "E se...")
2. Segunda pessoa ("você") em toda a narração — o espectador VIVE a experiência
3. Cada cena começa com um marcador de tempo (Dia 1, Semana 2, Ano 3, Hora 5, 1 mês...) que escala com a história
4. Escalada progressiva obrigatória: mudança leve → consequência extrema → fim irreversível/chocante
5. Frases curtas e sensoriais. Corta rápido. Sem enrolação.
6. Última cena: UMA frase final, chocante, definitiva.

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
      "prompt_imagem": "Image prompt in English. Skull narrator in foreground. Scene background, dramatic lighting, cinematic. Max 60 words.",
      "texto_legenda": "Trecho desta cena começando com o marcador de tempo"
    }}
  ],
  "hashtags": ["#shorts", "#tiktok", "#viral"]
}}

Gere exatamente {NUM_CENAS} cenas. Durações devem somar {DURACAO_ALVO}s.
"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2500,
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


# ─── Etapa 3: Gerar Narração com ElevenLabs ───────────────────────────────────

def gerar_narracao(roteiro: dict) -> Path:
    print("\n🎙️  [3/6] Gerando narração com ElevenLabs...")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": roteiro["narracao_completa"],
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.35,
                "similarity_boost": 0.85,
                "style": 0.7,
                "use_speaker_boost": True
            }
        }
    )
    response.raise_for_status()
    audio_path = OUTPUT_DIR / "narracao.mp3"
    audio_path.write_bytes(response.content)
    print(f"   ✅ Narração salva")
    return audio_path


# ─── Etapa 4: Gerar Imagens com Grok (xAI Aurora) ────────────────────────────

def gerar_imagem_grok(cena: dict, descricao_caveira: str, img_b64: str,
                      media_type: str, indice: int) -> Path:
    caminho = OUTPUT_DIR / f"cena_{indice+1:02d}.png"
    if caminho.exists():
        print(f"   ♻️  Cena {indice+1} já existe, pulando...")
        return caminho

    print(f"   🎨 Gerando imagem {indice+1}/{NUM_CENAS}...")

    prompt_final = (
        f"The skull narrator character: {descricao_caveira}. "
        f"Scene: {cena['prompt_imagem']}. "
        f"Style: cinematic, dramatic lighting, dark atmosphere, "
        f"vertical 9:16 composition, high quality, detailed, no text, no watermarks."
    )

    xai_client.chat.completions.create(
        model="grok-2-vision-latest",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {
                    "url": f"data:{media_type};base64,{img_b64}", "detail": "high"}},
                {"type": "text", "text": f"Use this skull as visual reference. Generate: {prompt_final}"}
            ]
        }],
        max_tokens=100
    )

    for tentativa in range(3):
        try:
            resp = xai_client.images.generate(
                model="aurora",
                prompt=prompt_final,
                n=1,
                size="1024x1792",
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
    print("\n🎨 [4/6] Gerando imagens com Grok...")
    img_b64, media_type = imagem_para_base64(imagem_path)
    imagens = []
    for i, cena in enumerate(roteiro["cenas"]):
        imagens.append(gerar_imagem_grok(cena, descricao_caveira, img_b64, media_type, i))
        time.sleep(1)
    return imagens


# ─── Etapa 5: Gerar Legendas SRT ──────────────────────────────────────────────

def gerar_legendas(roteiro: dict, audio_path: Path) -> Path:
    print("\n📄 [5/6] Gerando legendas...")

    duracao_real       = get_audio_duration(audio_path)
    fator              = duracao_real / DURACAO_ALVO
    PALAVRAS_POR_BLOCO = 3

    srt = ""
    # Subtitles começam após o hook
    t   = HOOK_DUR
    idx = 1

    for cena in roteiro["cenas"]:
        dur_cena  = cena["duracao_segundos"] * fator
        palavras  = cena["texto_legenda"].split()
        blocos    = [palavras[j:j+PALAVRAS_POR_BLOCO]
                     for j in range(0, len(palavras), PALAVRAS_POR_BLOCO)]
        dur_bloco = dur_cena / max(len(blocos), 1)

        for bloco in blocos:
            fim  = t + dur_bloco - 0.05
            srt += f"{idx}\n{ts(t)} --> {ts(fim)}\n{' '.join(bloco)}\n\n"
            idx += 1
            t   += dur_bloco

    path = OUTPUT_DIR / "legendas.srt"
    path.write_text(srt, encoding="utf-8")
    print(f"   ✅ {idx-1} blocos (offset: {HOOK_DUR:.1f}s | narração: {duracao_real:.1f}s)")
    return path


# ─── Etapa 6: Montagem Final ───────────────────────────────────────────────────

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
    # Silêncio base de 3s
    silencio = OUTPUT_DIR / "hook_silencio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc=0:s=44100:d={HOOK_DUR}",
        str(silencio)
    ], check=True, capture_output=True)

    n_clicks     = HOOK_N_CLIPS - 1   # 3 clicks
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
        # offset = tempo no stream de saída em que o xfade começa
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


def montar_video(imagens: list[Path], audio: Path, legendas: Path,
                 roteiro: dict) -> Path:
    print("\n🎞️  [6/6] Montando vídeo...")

    duracao_real   = get_audio_duration(audio)
    fator          = duracao_real / DURACAO_ALVO
    duracoes_reais = [c["duracao_segundos"] * fator for c in roteiro["cenas"]]

    # 6a. Ken Burns em cada cena
    clipes = renderizar_clipes_ken_burns(imagens, duracoes_reais)

    # 6b. SFX
    click  = gerar_sfx_click()
    whoosh = gerar_sfx_whoosh()

    # 6c. Hook: vídeo + áudio com clicks
    print("   🎣 Criando hook intro (3s)...")
    hook_video = criar_hook_video(clipes, duracoes_reais)
    hook_audio = criar_hook_audio(click)

    # 6d. Cenas principais com xfade
    print("   🔀 Aplicando transições xfade...")
    video_trans = concatenar_com_xfade(clipes, duracoes_reais)

    # 6e. Narração + whoosh nas transições
    print("   🔊 Mixando whoosh nas transições...")
    audio_com_whoosh = mixar_whoosh_na_narracao(audio, whoosh, duracoes_reais)

    # 6f. Concat hook + cenas (vídeo)
    lista_v = OUTPUT_DIR / "lista_video_full.txt"
    lista_v.write_text(
        f"file '{hook_video.absolute()}'\nfile '{video_trans.absolute()}'"
    )
    video_completo = OUTPUT_DIR / "video_completo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lista_v), "-c", "copy", str(video_completo)
    ], check=True, capture_output=True)

    # 6g. Concat hook_audio + narração (áudio)
    lista_a = OUTPUT_DIR / "lista_audio_full.txt"
    lista_a.write_text(
        f"file '{hook_audio.absolute()}'\nfile '{audio_com_whoosh.absolute()}'"
    )
    audio_completo = OUTPUT_DIR / "audio_completo.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lista_a), "-c", "copy", str(audio_completo)
    ], check=True, capture_output=True)

    # Duração total: xfade consome (N-1)*TRANS_DUR do vídeo
    total_dur = HOOK_DUR + duracao_real - (NUM_CENAS - 1) * TRANS_DUR

    subtitle_style = (
        f"subtitles={ffmpeg_path(legendas)}:"
        "force_style='Fontname=Arial Black,Fontsize=22,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "Outline=3,Shadow=0,Alignment=2,MarginV=350'"
    )
    saida = OUTPUT_DIR / f"{sanitize_filename(roteiro['titulo'])}.mp4"

    # 6h. Assembly final: vídeo + áudio + legendas + bg music (opcional)
    if BG_MUSIC.exists():
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_completo),
            "-i", str(audio_completo),
            "-i", str(BG_MUSIC),
            "-filter_complex",
            "[1:a]volume=1.0[narr];"
            "[2:a]volume=0.15,aloop=loop=-1:size=2e+09[bg];"
            "[narr][bg]amix=inputs=2:duration=first[aout]",
            "-vf", subtitle_style,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_dur), str(saida)
        ], check=True, capture_output=True)
        print("   🎵 Música de fundo mixada")
    else:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_completo),
            "-i", str(audio_completo),
            "-vf", subtitle_style,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
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
        (ANTHROPIC_API_KEY,  "ANTHROPIC_API_KEY"),
        (XAI_API_KEY,        "XAI_API_KEY"),
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
    imagens      = gerar_todas_imagens(roteiro, desc_caveira, imagem_path)
    legendas     = gerar_legendas(roteiro, audio)
    video        = montar_video(imagens, audio, legendas, roteiro)

    print(f"\n🎉 Pronto em {(time.time()-inicio)/60:.1f} minutos!")
    print(f"📁 {video}")
    print(f"🏷️  {' '.join(roteiro['hashtags'])}")


if __name__ == "__main__":
    main()
