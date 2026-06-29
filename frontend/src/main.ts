// SPA entry point: wires the auth widget, registers routes, starts the router.

import { route, startRouter } from "./router.ts";
import { renderPipelines } from "./views/pipelines.ts";
import { renderJobs } from "./views/jobs.ts";
import { renderJobDetail } from "./views/jobDetail.ts";
import { mountAuthWidget } from "./authWidget.ts";

function bootstrap(): void {
  const app = document.getElementById("app");
  const authSlot = document.getElementById("auth-slot");
  if (!app || !authSlot) {
    throw new Error("SPA mount points missing from index.html");
  }

  mountAuthWidget(authSlot);

  route("/", renderPipelines);
  route("/pipelines", renderPipelines);
  route("/jobs", renderJobs);
  route("/jobs/:id", renderJobDetail);

  startRouter(app);
}

bootstrap();
