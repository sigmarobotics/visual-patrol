// verify-share Edge Function
// Deploy with: supabase functions deploy verify-share --no-verify-jwt
// The --no-verify-jwt flag is required because unauthenticated external users call this endpoint.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { SignJWT } from "https://deno.land/x/jose@v5.2.0/index.ts";
import { compareSync } from "https://deno.land/x/bcrypt@v0.4.1/mod.ts";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

Deno.serve(async (req: Request): Promise<Response> => {
  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  let body: { token?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  const { token, password } = body;

  if (!token || !password) {
    return new Response(
      JSON.stringify({ error: "Missing required fields: token and password" }),
      {
        status: 400,
        headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
      }
    );
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const jwtSecret = Deno.env.get("SHARE_JWT_SECRET") ?? serviceRoleKey;

  if (!supabaseUrl || !serviceRoleKey || !jwtSecret) {
    console.error("Missing required environment variables");
    return new Response(JSON.stringify({ error: "Internal server error" }), {
      status: 500,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });

  // Look up the share link by token
  const { data: shareLink, error: shareLinkError } = await supabase
    .from("share_links")
    .select("id, site_id, password_hash, expires_at")
    .eq("token", token)
    .single();

  if (shareLinkError || !shareLink) {
    return new Response(JSON.stringify({ error: "Share link not found" }), {
      status: 404,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  // Check expiry
  if (shareLink.expires_at) {
    const expiresAt = new Date(shareLink.expires_at);
    if (expiresAt < new Date()) {
      return new Response(JSON.stringify({ error: "Share link has expired" }), {
        status: 410,
        headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
      });
    }
  }

  // Verify password against bcrypt hash
  let passwordValid: boolean;
  try {
    passwordValid = compareSync(password, shareLink.password_hash);
  } catch (err) {
    console.error("bcrypt compare error:", err);
    return new Response(JSON.stringify({ error: "Internal server error" }), {
      status: 500,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  if (!passwordValid) {
    return new Response(JSON.stringify({ error: "Invalid password" }), {
      status: 401,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  // Fetch site name from sites table
  const { data: site, error: siteError } = await supabase
    .from("sites")
    .select("name")
    .eq("id", shareLink.site_id)
    .single();

  if (siteError || !site) {
    console.error("Failed to fetch site:", siteError);
    return new Response(JSON.stringify({ error: "Internal server error" }), {
      status: 500,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  // Sign a JWT with { site_id, site_name, role: "viewer" }, 24h expiry
  let accessToken: string;
  try {
    const secretBytes = new TextEncoder().encode(jwtSecret);
    accessToken = await new SignJWT({
      site_id: shareLink.site_id,
      site_name: site.name,
      role: "viewer",
    })
      .setProtectedHeader({ alg: "HS256" })
      .setIssuedAt()
      .setExpirationTime("24h")
      .sign(secretBytes);
  } catch (err) {
    console.error("JWT signing error:", err);
    return new Response(JSON.stringify({ error: "Internal server error" }), {
      status: 500,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  return new Response(
    JSON.stringify({ access_token: accessToken, site_name: site.name }),
    {
      status: 200,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    }
  );
});
