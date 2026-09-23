// Express-style TypeScript API: require() import, middleware via app.use,
// route registrations, an interface with a class implementor, and a
// callable alias used as a route handler.
const express = require("express");

interface Greeter {
  greet(name: string): string;
  wave(): void;
}

function joinHi(a: string, b: string): string {
  return a + " " + b;
}

class Friendly implements Greeter {
  greet(name: string): string {
    return joinHi("hi", name);
  }
  wave(): void {}
}

const app = express();

function audit(req: any, res: any, next: any): void {
  next();
}
app.use(audit);

function listItems(req: any, res: any): void {
  const g = new Friendly();
  const name = String(req.query.name || "");
  res.json({ echo: g.greet(name) });
}

// Callable alias: the same handler bound through a const binding.
const aliasList = listItems;

app.get("/api/items", listItems);
app.get("/api/items/alias", aliasList);

export { app, listItems, aliasList, Friendly, Greeter, joinHi };
