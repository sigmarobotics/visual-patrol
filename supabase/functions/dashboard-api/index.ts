// dashboard-api Edge Function
// Deploy with: supabase functions deploy dashboard-api --no-verify-jwt
// Proxy for cloud dashboard — queries data with service_role key,
// verifying session tokens signed by verify-share.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jwtVerify } from "https://deno.land/x/jose@v5.2.0/index.ts";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

async function verifySiteToken(req: Request, secret: string): Promise<string | null> {
  const auth = req.headers.get("Authorization");
  if (!auth?.startsWith("Bearer ")) return null;
  try {
    const { payload } = await jwtVerify(
      auth.slice(7),
      new TextEncoder().encode(secret)
    );
    return (payload as any).site_id ?? null;
  } catch {
    return null;
  }
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;

  // Verify session token (signed by verify-share with same service_role key)
  const siteId = await verifySiteToken(req, serviceRoleKey);
  if (!siteId) {
    return jsonResponse({ error: "Unauthorized" }, 401);
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });

  let body: { action: string; params?: Record<string, any> };
  try {
    body = await req.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON" }, 400);
  }

  const { action, params } = body;

  try {
    switch (action) {
      case "robots": {
        const { data, error } = await supabase
          .from("robots")
          .select("robot_id, robot_name")
          .eq("site_id", siteId)
          .order("robot_name");
        if (error) return jsonResponse({ error: error.message }, 500);
        return jsonResponse(data);
      }

      case "history": {
        let query = supabase
          .from("patrol_runs")
          .select("id, local_id, robot_id, status, start_time, end_time, total_tokens")
          .eq("site_id", siteId)
          .order("local_id", { ascending: false })
          .limit(100);
        if (params?.robot_id) query = query.eq("robot_id", params.robot_id);
        const { data, error } = await query;
        if (error) return jsonResponse({ error: error.message }, 500);

        // Fetch robot names
        const { data: robots } = await supabase
          .from("robots")
          .select("robot_id, robot_name")
          .eq("site_id", siteId);
        const robotMap = Object.fromEntries((robots ?? []).map(r => [r.robot_id, r.robot_name]));
        const enriched = (data ?? []).map(r => ({ ...r, robot_name: robotMap[r.robot_id] ?? r.robot_id }));
        return jsonResponse(enriched);
      }

      case "run-detail": {
        if (!params?.run_id) return jsonResponse({ error: "Missing run_id" }, 400);
        const { data: run, error: runErr } = await supabase
          .from("patrol_runs")
          .select("*")
          .eq("id", params.run_id)
          .eq("site_id", siteId)
          .single();
        if (runErr) return jsonResponse({ error: runErr.message }, 500);

        const { data: inspections } = await supabase
          .from("inspection_results")
          .select("*")
          .eq("run_id", params.run_id)
          .eq("site_id", siteId)
          .order("local_id");

        const { data: alerts } = await supabase
          .from("edge_ai_alerts")
          .select("*")
          .eq("run_id", params.run_id)
          .eq("site_id", siteId)
          .order("local_id");

        return jsonResponse({ run, inspections: inspections ?? [], alerts: alerts ?? [] });
      }

      case "reports": {
        const { data, error } = await supabase
          .from("generated_reports")
          .select("*")
          .eq("site_id", siteId)
          .order("local_id", { ascending: false })
          .limit(50);
        if (error) return jsonResponse({ error: error.message }, 500);
        return jsonResponse(data);
      }

      case "token-stats": {
        let query = supabase
          .from("patrol_runs")
          .select("robot_id, start_time, inspection_input_tokens, inspection_output_tokens, report_input_tokens, report_output_tokens, telegram_input_tokens, telegram_output_tokens, video_input_tokens, video_output_tokens")
          .eq("site_id", siteId)
          .not("start_time", "is", null);
        if (params?.robot_id) query = query.eq("robot_id", params.robot_id);
        if (params?.date_from) query = query.gte("start_time", params.date_from);
        if (params?.date_to) query = query.lte("start_time", params.date_to + "T23:59:59");
        query = query.order("start_time");
        const { data, error } = await query;
        if (error) return jsonResponse({ error: error.message }, 500);
        return jsonResponse(data);
      }

      default:
        return jsonResponse({ error: `Unknown action: ${action}` }, 400);
    }
  } catch (err) {
    console.error("dashboard-api error:", err);
    return jsonResponse({ error: "Internal server error" }, 500);
  }
});
