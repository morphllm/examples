import { createServer } from "node:http";

type Handler = (body: unknown) => Promise<unknown>;

const routes = new Map<string, Handler>();

export function route(path: string, handler: Handler): void {
  routes.set(path, handler);
}

export const server = createServer(async (req, res) => {
  const handler = routes.get(req.url ?? "");
  if (!handler) {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }

  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(chunk as Buffer);
  }
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined;

  const result = await handler(body);
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(result));
});
