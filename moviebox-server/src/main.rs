use axum::{extract::{Query, State}, routing::get, Json, Router};
use moviebox_tui::providers::moviebox::client::MovieBoxClient;
use moviebox_tui::providers::{Provider, ReleaseProvider};
use serde::Deserialize;
use std::sync::Arc;

#[derive(Deserialize)]
struct SearchQuery {
    q: String,
    page: Option<usize>,
}

#[derive(Deserialize)]
struct IdQuery {
    id: String,
}

#[derive(Deserialize)]
struct StreamQuery {
    id: String,
    season: usize,
    episode: usize,
}

#[tokio::main]
async fn main() {
    let client = Arc::new(MovieBoxClient::new());
    
    // trigger a search to init the auth tokens internally
    let _ = client.search("a", 1).await; 

    let app = Router::new()
        .route("/search", get(search))
        .route("/details", get(details))
        .route("/stream", get(stream))
        .route("/captions", get(captions))
        .with_state(client);

    let port = std::env::var("MB_PORT").unwrap_or_else(|_| "8000".to_string());
    let bind_addr = format!("0.0.0.0:{}", port);
    let listener = tokio::net::TcpListener::bind(&bind_addr).await.unwrap();
    println!("API server running on http://{}", bind_addr);
    axum::serve(listener, app).await.unwrap();
}

async fn search(
    State(client): State<Arc<MovieBoxClient>>,
    Query(params): Query<SearchQuery>,
) -> Json<serde_json::Value> {
    match client.search(&params.q, params.page.unwrap_or(1)).await {
        Ok(res) => Json(serde_json::json!({ "success": true, "data": res })),
        Err(e) => Json(serde_json::json!({ "success": false, "error": e.to_string() }))
    }
}

async fn details(
    State(client): State<Arc<MovieBoxClient>>,
    Query(params): Query<IdQuery>,
) -> Json<serde_json::Value> {
    match client.details(&params.id).await {
        Ok(res) => Json(serde_json::json!({ "success": true, "data": res })),
        Err(e) => Json(serde_json::json!({ "success": false, "error": e.to_string() }))
    }
}

async fn stream(
    State(client): State<Arc<MovieBoxClient>>,
    Query(params): Query<StreamQuery>,
) -> Json<serde_json::Value> {
    match client.episode_streams(&params.id, params.season, params.episode).await {
        Ok(res) => Json(serde_json::json!({ "success": true, "data": res })),
        Err(e) => Json(serde_json::json!({ "success": false, "error": e.to_string() }))
    }
}

#[derive(Deserialize)]
struct CaptionsQuery {
    id: String,
    season: usize,
    episode: usize,
    resource_id: String,
}

async fn captions(
    State(client): State<Arc<MovieBoxClient>>,
    Query(params): Query<CaptionsQuery>,
) -> Json<serde_json::Value> {
    match client.get_ext_captions(&params.id, &params.resource_id).await {
        Ok(res) => Json(serde_json::json!({ "success": true, "data": res })),
        Err(e) => Json(serde_json::json!({ "success": false, "error": e.to_string() }))
    }
}
