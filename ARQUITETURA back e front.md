# 🛍️ Achadinhos do Momento — Guia de Arquitetura & Deploy

## 1. ARQUITETURA GERAL

```
┌─────────────────────────────────────────────────────────────────┐
│                    FLUXO DA APLICAÇÃO                           │
│                                                                 │
│  Instagram/TikTok         Bio Page (front-end)                  │
│  Bio Link  ──────────►  [ index.html no Vercel/Netlify ]        │
│                                   │                             │
│                                   │ GET /links                  │
│                                   ▼                             │
│                         [ FastAPI no Render ]                   │
│                                   │                             │
│                                   │ SQL queries                 │
│                                   ▼                             │
│                         [ SQLite (persistido) ]                 │
│                                                                 │
│  Instagram comentário             DM Automática                 │
│  "EU QUERO"  ──────►  Meta Webhook ──► FastAPI /webhook/meta    │
│                                             │                   │
│                                             ▼                   │
│                                   Graph API → envia DM          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. PLATAFORMAS DE HOSPEDAGEM (CUSTO ZERO)

| Componente      | Plataforma Recomendada | Alternativa       | Observação                        |
|-----------------|------------------------|-------------------|-----------------------------------|
| **Frontend**    | Vercel                 | Netlify           | Deploy via drag-and-drop do HTML  |
| **Backend API** | Render (Free Web)      | PythonAnywhere    | 512MB RAM, dorme após 15min ociosos|
| **Banco**       | SQLite no próprio Render | Supabase (PostgreSQL) | Render Free inclui 1GB de disco persistente |

> **Limitação Render Free**: o serviço "dorme" após 15 min sem uso. O primeiro acesso do dia leva ~30 segundos. Para mitigar, use um serviço de ping gratuito como [UptimeRobot](https://uptimerobot.com) (grátis, faz ping a cada 5 min).

---

## 3. ESTRUTURA DE ARQUIVOS

```
achadinhos/
├── backend/
│   ├── main.py           ← API FastAPI + Webhook
│   ├── requirements.txt
│   └── render.yaml       ← config de deploy do Render
└── frontend/
    └── index.html        ← Página de links (deploy no Vercel)
```

---

## 4. DEPLOY PASSO A PASSO

### 4.1 Backend no Render

1. Crie conta em https://render.com
2. New → Web Service → conecte seu repositório GitHub
3. Selecione a pasta `backend/`
4. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Region**: Ohio (US East) — mais próximo do Brasil com free tier
5. Em **Environment Variables**, adicione:
   ```
   WEBHOOK_VERIFY_TOKEN  = qualquer_string_secreta_que_voce_inventar
   META_APP_SECRET       = (vem do painel Meta Developers)
   PAGE_ACCESS_TOKEN     = (vem do painel Meta Developers)
   ADMIN_SECRET          = senha_para_gerenciar_seus_links
   DATABASE_URL          = sqlite:////data/achadinhos.db
   ```
6. Em **Disk** → Add Disk: nome `sqlite-data`, mount path `/data`, size `1 GB`
7. Clique em **Create Web Service**
8. Copie a URL gerada: `https://achadinhos-api.onrender.com`

### 4.2 Frontend no Vercel

1. Crie conta em https://vercel.com
2. New Project → Import → selecione a pasta `frontend/`
   **OU** use o método mais rápido: drag-and-drop do `index.html` em https://vercel.com/new
3. Antes do deploy, edite no `index.html` a linha:
   ```js
   const API_BASE = "https://achadinhos-api.onrender.com"; // sua URL do Render
   ```
4. Deploy → copie a URL do front-end (ex: `https://achadinhos.vercel.app`)
5. Coloque essa URL na Bio do seu Instagram/TikTok

---

## 5. CONFIGURAÇÃO DO WEBHOOK NO PAINEL META DEVELOPERS

### Pré-requisitos
- Conta Business no Facebook
- App criado no Meta Developers (https://developers.facebook.com)
- Página do Facebook vinculada ao Instagram Profissional

### Passo a Passo

```
1. Acesse: https://developers.facebook.com/apps/
2. Crie um App → Tipo: "Business"
3. No Dashboard do App → Add Product → "Webhooks"
4. Clique em "Instagram" → "Subscribe to this object"

5. Preencha:
   ┌────────────────────────────────────────────────────────┐
   │ Callback URL:  https://sua-api.onrender.com/webhook/meta
   │ Verify Token:  o mesmo valor de WEBHOOK_VERIFY_TOKEN
   └────────────────────────────────────────────────────────┘

6. Clique em "Verify and Save"
   → Meta faz GET na sua URL; se retornar o hub.challenge, é aprovado ✅

7. Em "Subscription Fields", marque: ✅ comments  ✅ messages

8. Vá em Settings → Basic:
   - Copie o "App Secret" → coloque em META_APP_SECRET no Render

9. Vá em Instagram → Tokens de Acesso da Página:
   - Gere o Page Access Token (token de longa duração)
   - Coloque em PAGE_ACCESS_TOKEN no Render

10. Em App Review → Permissions:
    - Solicite: instagram_manage_comments, instagram_manage_messages
    (Para testes em desenvolvimento, funciona sem aprovação)
```

> ⚠️ **Importante**: Para enviar DMs automaticamente para qualquer usuário, o App precisa estar em modo **Live** com as permissões aprovadas. Em modo de desenvolvimento, só funciona para usuários de teste cadastrados no painel.

---

## 6. GERENCIANDO SEUS LINKS (API Admin)

Use qualquer cliente HTTP (Insomnia, Postman, ou `curl`):

### Listar todos os links
```bash
curl https://sua-api.onrender.com/links
```

### Criar um novo link
```bash
curl -X POST https://sua-api.onrender.com/links \
  -H "Content-Type: application/json" \
  -H "x-admin-secret: SUA_ADMIN_SECRET" \
  -d '{
    "title": "Tênis Nike em promoção 🔥",
    "url": "https://shopee.com.br/seu_link_afiliado",
    "emoji": "👟",
    "badge": "OFERTA",
    "badge_color": "#e11d48",
    "order": 0
  }'
```

### Deletar um link
```bash
curl -X DELETE https://sua-api.onrender.com/links/1 \
  -H "x-admin-secret: SUA_ADMIN_SECRET"
```

### Adicionar nova palavra-chave para DM
```bash
# Direto no SQLite via render shell, ou crie uma rota admin para KeywordLink
sqlite3 /data/achadinhos.db \
  "INSERT INTO keywordlink (keyword, url, message) VALUES ('LINK', 'https://shopee.com.br/novo_link', 'Aqui está! 👇 {url}');"
```

---

## 7. CHECKLIST DE LANÇAMENTO

- [ ] Repositório no GitHub com as pastas `backend/` e `frontend/`
- [ ] Backend deployado no Render + variáveis de ambiente preenchidas
- [ ] Disk persistente configurado no Render
- [ ] `API_BASE` atualizado no `index.html`
- [ ] Frontend deployado no Vercel
- [ ] URL do front na bio do Instagram/TikTok
- [ ] Webhook configurado e verificado no painel Meta
- [ ] Permissões do App Meta solicitadas (ou modo dev configurado)
- [ ] UptimeRobot configurado para pingar a API a cada 5 min
- [ ] Primeiro teste: comentar "EU QUERO" no Reels e verificar DM

---

## 8. PRÓXIMOS PASSOS (QUANDO VALIDADO)

| Upgrade                     | Ferramenta          | Custo    |
|-----------------------------|---------------------|----------|
| Banco escalável             | Supabase PostgreSQL | Grátis   |
| API sem cold start          | Railway ($5/mês)    | ~R$25/mês|
| Encurtador de URL rastreável| Bit.ly / Dub.co     | Grátis   |
| Painel admin visual         | Retool / AppSmith   | Grátis   |
| Analytics de cliques        | Umami (self-hosted) | Grátis   |
