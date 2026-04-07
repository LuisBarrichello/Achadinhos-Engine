# 🤖 Bot Garimpeiro (Telegram) + 📲 Cross-posting Organizer

---

## PARTE 3 — Bot Telegram "Garimpeiro e Publicador"

### Arquitetura do Bot

```
┌──────────────────────────────────────────────────────────────┐
│                    FLUXO DO GARIMPEIRO                       │
│                                                              │
│  APScheduler (a cada 30min)                                  │
│       │                                                      │
│       ▼                                                      │
│  fetch_pelando_deals()  ←── RSS Feed do Pelando.com.br       │
│  fetch_custom_rss()     ←── Seus RSS personalizados          │
│       │                                                      │
│       ▼                                                      │
│  Filtra por desconto >= MIN_DISCOUNT_PCT (padrão: 20%)       │
│       │                                                      │
│       ▼                                                      │
│  inject_affiliate_params() ←── Regex injeta seu ID           │
│       │                                                      │
│       ▼                                                      │
│  Verifica duplicata no SQLite                                │
│       │                                                      │
│       ▼                                                      │
│  bot.send_message() → Canal @achadinhosdomomento             │
└──────────────────────────────────────────────────────────────┘
```

### Deploy no Render (Free Tier)

1. Suba a pasta `telegram_bot/` no GitHub
2. Render → New → **Background Worker** (não Web Service!)
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python garimpeiro.py`
5. Adicione as variáveis de ambiente:

```
TELEGRAM_BOT_TOKEN        = token do @BotFather
TELEGRAM_CHANNEL_ID       = @achadinhosdomomento (ou -100xxxxxxxx)
ADMIN_TELEGRAM_USER_ID    = seu ID do Telegram (use @userinfobot para descobrir)
SHOPEE_AFFILIATE_ID       = seu sub_id do programa de afiliados Shopee
ML_AFFILIATE_ID           = seu publisher_id do ML Afiliados
MIN_DISCOUNT_PCT          = 20
POLL_INTERVAL_MIN         = 30
```

> **Nota sobre Render Free**: Background Workers não dormem no plano free — eles ficam ativos 24/7 dentro do limite mensal de horas gratuitas. Ideal para bots.

### Como criar o Bot no Telegram (BotFather)

```
1. Abra o Telegram e busque: @BotFather
2. /newbot
3. Dê um nome: "Achadinhos Garimpeiro"
4. Dê um username: @achadinhos_garimpeiro_bot
5. Copie o TOKEN gerado → coloque em TELEGRAM_BOT_TOKEN

6. Crie seu Canal público: @achadinhosdomomento
7. Adicione o bot como ADMINISTRADOR do canal
   (sem isso ele não consegue postar)

8. Para descobrir seu ID pessoal:
   Abra @userinfobot e envie qualquer mensagem
   Copie o "Id" → coloque em ADMIN_TELEGRAM_USER_ID
```

### Comandos disponíveis (chat privado com o bot)

| Comando | Ação |
|---------|------|
| `/start` | Mostra ajuda |
| `/garimpar` | Roda um ciclo de garimpo agora |
| `/status` | Mostra estatísticas (total postado, hoje) |
| `/postar Título \| URL` | Posta uma oferta manualmente |

### Adicionando novas fontes de garimpo

**Opção A — Adicionar feed RSS:**
```python
# Em garimpeiro.py, adicione na lista CUSTOM_RSS_FEEDS:
CUSTOM_RSS_FEEDS = [
    "https://www.pelando.com.br/api/feeds/deals?category=eletronicos",
    "https://clubededesconto.com/rss/ofertas",
]
```

**Opção B — API oficial Shopee Afiliados:**
```
1. Acesse: https://affiliate.shopee.com.br
2. Programa de Afiliados → Ferramentas → API
3. Use o endpoint GET /api/v2/product/get_item_list
   para puxar produtos em alta com seus parâmetros de afiliado nativos
```

**Opção C — API oficial ML Afiliados:**
```
1. Acesse: https://www.mercadolibre.com/afiliados
2. Gere suas credenciais OAuth
3. Endpoint: GET /sites/MLB/search?q={keyword}&sort=relevance
   Adicione &mt=SEU_CAMPAIGN_ID na URL para rastrear
```

---

## PARTE 4 — Cross-posting Organizer

### Por que NÃO automatizar o upload?

```
❌ API → Shadowban  →  Alcance orgânico zero
✅ Manual + áudio viral  →  Alcance máximo

O ganho de alcance de usar um áudio trending nativo
compensa 3 minutos de upload manual com folga.
```

### Como usar o Organizer

**Fluxo básico:**
```bash
# 1. Instale as dependências
pip install -r requirements.txt
# E instale o ffmpeg: https://ffmpeg.org/download.html

# 2. Coloque seus vídeos na pasta /queue
cp meu_video.mp4 queue/

# 3. (Opcional) Edite o queue.csv com metadados
# Preencha: título, preço, link de afiliado, plataformas

# 4. Rode o organizer
python organizer.py

# 5. Transfira /ready para o celular
# Use Google Drive, AirDrop ou cabo USB
```

**Resultado na pasta /ready:**
```
ready/
└── 01_tenis_nike/
    ├── instagram/
    │   ├── tenis_nike.mp4          ← vídeo pronto
    │   ├── thumbnail.jpg           ← thumbnail 1:1 (1080x1080)
    │   ├── caption_instagram.txt   ← caption com hashtags
    │   └── CHECKLIST_INSTAGRAM.txt ← passo a passo de postagem
    ├── tiktok/
    │   ├── tenis_nike.mp4
    │   ├── thumbnail.jpg           ← thumbnail 9:16 (1080x1920)
    │   ├── caption_tiktok.txt
    │   └── CHECKLIST_TIKTOK.txt
    └── meta.json                   ← metadados da oferta
```

### Rotina semanal sugerida (3 horas → 7 dias de conteúdo)

```
Domingo (2-3 horas):
  1. Grave/baixe 5-7 vídeos de produtos
  2. Coloque em /queue e edite o queue.csv
  3. Rode: python organizer.py
  4. Transfira /ready para o Google Drive
  5. Programe no calendário: 1 post/dia, 19h

Segunda a Domingo (3 min/dia):
  1. Abra o Google Drive no celular
  2. Baixe o vídeo da pasta do dia
  3. Abra Instagram/TikTok, importe o vídeo
  4. Escolha um áudio viral
  5. Cole a caption → Publicar ✅
```

---

## VISÃO GERAL DO PROJETO COMPLETO

```
┌─────────────────────────────────────────────────────────────┐
│              ACHADINHOS DO MOMENTO — ECOSSISTEMA            │
├──────────────┬──────────────────────────────────────────────┤
│  Módulo      │  Função                                      │
├──────────────┼──────────────────────────────────────────────┤
│  Bio Page    │  Converte visitantes em compradores           │
│  FastAPI     │  Gerencia links + recebe webhook Instagram    │
│  DM Bot Meta │  Converte comentários em vendas diretas       │
│  Telegram    │  Garante renda passiva com audiência própria  │
│  Organizer   │  Multiplica alcance com upload eficiente      │
└──────────────┴──────────────────────────────────────────────┘

Custo total da infraestrutura: R$ 0,00/mês
Tempo de gestão estimado: 3h/semana
```
