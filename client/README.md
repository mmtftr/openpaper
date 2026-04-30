# Client

This server manages the frontend for the Open Paper project, which allows users to upload, chat with, annotate, and manage research papers in one place.

First, ensure you've started the backend server. See `/server` for details.

For the full local Docker stack, run this from the repo root:

```bash
docker compose up --build client
```

For a fresh setup, run:

```bash
yarn go
```

Open [http://localhost:9002](http://localhost:9002) with your browser to use the Docker Compose app.

## Development
To run the development server, use:

```bash
yarn dev
```
