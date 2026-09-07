"""
Coleta o mercado do Cartola FC e gera data/mercado.json, consumido pelo app estático.

Une as duas versões anteriores do script:
- dados de mercado (atletas, clubes, posições, preço, médias)
- dados de partidas (mando de campo, adversário)

Melhorias em relação ao original:
- retries com backoff em caso de instabilidade da API (comum perto do fechamento do mercado)
- tratamento de erro por etapa, sem derrubar o processo inteiro
- "Mando favorável" no lugar de "Potencial SG": joga em casa não garante SG,
  então o nome não promete mais do que a heurística realmente mede
- saída em JSON, pronta pro app ler direto (sem CORS, sem backend)
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
URL_MERCADO = "https://api.cartola.globo.com/atletas/mercado"
URL_PARTIDAS = "https://api.cartola.globo.com/partidas"
STATUS_PROVAVEL = 7

SAIDA = Path(__file__).resolve().parent.parent / "data" / "mercado.json"


def buscar_json(url: str, tentativas: int = 3, espera: float = 2.0):
    """GET com retry simples. Retorna None se todas as tentativas falharem."""
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            ultimo_erro = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            ultimo_erro = str(exc)

        if tentativa < tentativas:
            time.sleep(espera * tentativa)

    print(f"[erro] Falha ao buscar {url}: {ultimo_erro}", file=sys.stderr)
    return None


def montar_mapa_confrontos(partidas: list, clubes: dict) -> dict:
    mapa = {}
    for partida in partidas:
        casa_id = partida.get("clube_casa_id")
        fora_id = partida.get("clube_visita_id")

        nome_casa = clubes.get(str(casa_id), {}).get("nome", "N/A")
        nome_fora = clubes.get(str(fora_id), {}).get("nome", "N/A")

        mapa[casa_id] = {"mando": "Casa", "adversario": nome_fora}
        mapa[fora_id] = {"mando": "Fora", "adversario": nome_casa}
    return mapa


def calcular_media_basica(pontos_num: float, jogos: int, scout: dict) -> float:
    gols = scout.get("G", 0) or 0
    assistencias = scout.get("A", 0) or 0
    pontos_g_a = (gols * 8.0) + (assistencias * 5.0)
    if jogos <= 0:
        return 0.0
    return round((pontos_num - pontos_g_a) / jogos, 2)


def processar_atletas(dados_mercado: dict, mapa_confrontos: dict) -> list:
    atletas = dados_mercado.get("atletas", [])
    clubes = dados_mercado.get("clubes", {})
    posicoes = dados_mercado.get("posicoes", {})

    processados = []
    for atleta in atletas:
        if atleta.get("status_id") != STATUS_PROVAVEL:
            continue

        clube_id = atleta.get("clube_id")
        posicao_id = str(atleta.get("posicao_id"))
        nome_posicao = posicoes.get(posicao_id, {}).get("nome", "N/A")
        scout = atleta.get("scout") or {}
        jogos = atleta.get("jogos_num", 0) or 0
        pontos_num = atleta.get("pontos_num", 0) or 0
        preco = atleta.get("preco_num", 0) or 0
        media = atleta.get("media_num", 0) or 0

        media_basica = calcular_media_basica(pontos_num, jogos, scout)
        min_para_valorizar = round(preco * 0.45, 2)

        info_confronto = mapa_confrontos.get(clube_id, {"mando": "N/A", "adversario": "N/A"})

        # "Mando favorável": heurística simples baseada em jogar em casa,
        # útil como sinal inicial, não como garantia de SG ou pontuação.
        mando_favoravel = "N/A"
        if posicao_id in ("1", "2", "3"):  # Goleiro, Lateral, Zagueiro
            mando_favoravel = "Favorável" if info_confronto["mando"] == "Casa" else "Neutro/Desfavorável"

        processados.append({
            "id": atleta.get("atleta_id"),
            "nome": atleta.get("apelido"),
            "clube": clubes.get(str(clube_id), {}).get("nome", "N/A"),
            "clube_abreviacao": clubes.get(str(clube_id), {}).get("abreviacao", ""),
            "posicao": nome_posicao,
            "preco": preco,
            "media_total": media,
            "media_basica": media_basica,
            "min_para_valorizar": min_para_valorizar,
            "jogos": jogos,
            "mando": info_confronto["mando"],
            "adversario": info_confronto["adversario"],
            "mando_favoravel": mando_favoravel,
        })
    return processados


def main():
    dados_mercado = buscar_json(URL_MERCADO)
    if dados_mercado is None:
        print("[erro] Não foi possível obter o mercado. Abortando sem sobrescrever o JSON.", file=sys.stderr)
        sys.exit(1)

    dados_partidas = buscar_json(URL_PARTIDAS)
    partidas = (dados_partidas or {}).get("partidas", [])
    if not partidas:
        print("[aviso] Nenhuma partida retornada pela API — mando/adversário ficarão em branco nesta atualização.", file=sys.stderr)

    clubes = dados_mercado.get("clubes", {})
    mapa_confrontos = montar_mapa_confrontos(partidas, clubes)

    lista_atletas = processar_atletas(dados_mercado, mapa_confrontos)

    saida = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "total_atletas": len(lista_atletas),
        "atletas": lista_atletas,
    }

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {len(lista_atletas)} atletas salvos em {SAIDA}")


if __name__ == "__main__":
    main()
