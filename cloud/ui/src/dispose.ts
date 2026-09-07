/* ------------------------------------------------------------------ *
 * Releasing what WebGL will not release for you.
 *
 * A garbage-collected buffer is not a freed buffer: geometries,
 * materials, textures, render targets and the context itself are held by
 * the driver until something calls `dispose`. Switching between the flat
 * view and the 3D one unmounts the renderer, and a browser will hand out
 * only a handful of WebGL contexts before it starts killing the oldest —
 * so a mode switch that leaks one is a view that stops working after a
 * few toggles, which is a failure, not an inefficiency.
 *
 * Nothing here imports three.js. It is duck-typed on `dispose()` so it
 * can be tested without a GPU, and so the test can assert the one thing
 * that actually matters: that everything registered was released, once.
 * ------------------------------------------------------------------ */

/** Anything three.js will let go of when asked. */
export interface Releasable {
  dispose(): void;
}

export type Release = Releasable | (() => void);

/**
 * A list of things to release, in reverse order of acquisition.
 *
 * Reverse because that is the order in which they stop being referenced:
 * the renderer is made first and torn down last, after the geometries it
 * was drawing.
 */
export class Disposer {
  private items: Release[] = [];
  private done = false;

  /** Register and hand back, so a resource can be made and kept in one line. */
  add<T extends Release>(item: T): T {
    if (this.done) {
      /* Registered after teardown — a late async arrival. Release it now
         rather than holding it for a `dispose` that has already run. */
      run(item);
      return item;
    }
    this.items.push(item);
    return item;
  }

  get size(): number {
    return this.items.length;
  }

  get disposed(): boolean {
    return this.done;
  }

  /**
   * Release everything. Idempotent: calling it twice releases nothing the
   * second time, which matters because React may unmount a component
   * whose own cleanup has already run under StrictMode.
   *
   * One resource that throws must not strand the rest — a context that is
   * already lost throws from half its methods — so each is guarded and
   * the failures are counted rather than raised.
   */
  dispose(): { released: number; failed: number } {
    if (this.done) return { released: 0, failed: 0 };
    this.done = true;
    let released = 0;
    let failed = 0;
    for (let i = this.items.length - 1; i >= 0; i -= 1) {
      if (run(this.items[i])) released += 1;
      else failed += 1;
    }
    this.items.length = 0;
    return { released, failed };
  }
}

function run(item: Release): boolean {
  try {
    if (typeof item === "function") item();
    else item.dispose();
    return true;
  } catch {
    return false;
  }
}
