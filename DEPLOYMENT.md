# Deployment

Recommended setup for interviews:

- Backend: Render Docker Web Service
- Database: Render PostgreSQL
- Redis: Render Redis
- Frontend: Vercel, with root directory set to `frontend`

## Backend on Render

Use `render.yaml` as a Blueprint from the repository root.

Required env vars:

- `GOOGLE_API_KEY`
- `FRONTEND_ORIGIN` after Vercel gives you the frontend URL

Optional env vars:

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`

The backend container runs:

```bash
alembic upgrade head
python seed.py
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health check:

```bash
GET /health
```

## Frontend on Vercel

Import the same Git repository and set:

- Root Directory: `frontend`
- `BACKEND_URL=https://<your-render-backend>.onrender.com`
- `NEXT_PUBLIC_API_URL=https://<your-render-backend>.onrender.com`

After the Vercel deployment succeeds, copy the frontend URL back to Render:

```bash
FRONTEND_ORIGIN=https://<your-vercel-app>.vercel.app
```

Redeploy the backend once so CORS uses the final frontend origin.

## Demo Checks

Open:

- `https://<frontend-url>/`
- `https://<frontend-url>/dashboard`
- `https://<backend-url>/health`

Try these messages:

```text
订单 123456 申请退款，商品破损
订单 456789 申请退款
七天无理由退款怎么算？什么情况下不能退？
```
