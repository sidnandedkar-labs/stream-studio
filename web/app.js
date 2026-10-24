"use strict";
const $ = id => document.getElementById(id);
let history = [], exports = [], controller = null, config = null;
const seconds = n => n == null ? "—" : `${n.toFixed(2)} s`;
function message(role) {
  const empty = $("chat").querySelector(".empty"); if (empty) empty.remove();
  const box = document.createElement("article"); box.className = `message ${role}`;
  const heading = document.createElement("h2"); heading.textContent = role;
  const text = document.createElement("p"); box.append(heading, text); $("chat").append(box);
  return {box, text};
}
function busy(value) {
  for (const id of ["send", "clear", "prompt"]) $(id).disabled = value;
  $("stop").disabled = !value;
}
$("form").addEventListener("submit", async event => {
  event.preventDefault(); if (controller || !config) return;
  const prompt = $("prompt").value.trim(); if (!prompt) return;
  if (history.length >= 38) { $("status").textContent = "Start a new chat before adding another turn."; return; }
  history.push({role:"user",content:prompt}); message("user").text.textContent = prompt;
  const answer = message("assistant"); let content = "", reasoning = "", metrics = null, completed = false;
  const reasoningBox = document.createElement("details"), summary = document.createElement("summary"), pre = document.createElement("div");
  summary.textContent = "Reasoning"; pre.className = "reasoning"; reasoningBox.append(summary,pre);
  $("prompt").value = ""; busy(true); controller = new AbortController();
  $("status").textContent = "Waiting for the first output…";
  for (const id of ["first","elapsed","speed"]) $(id).textContent="—";
  $("chars").textContent="0";
  const started = performance.now(); let seen = false;
  const ticker = setInterval(()=>$("elapsed").textContent=seconds((performance.now()-started)/1000),100);
  function receive(data) {
    if (data.type === "error") throw new Error(data.message);
    if (data.type === "metrics") { metrics=data; completed=true; return; }
    if (!["content","reasoning_content"].includes(data.type)) return;
    if (!seen) { seen=true; $("first").textContent=seconds(data.elapsed_s); }
    if (data.type === "content") { content+=data.text; answer.text.textContent=content; }
    else { reasoning+=data.text; if (!reasoningBox.parentNode) answer.box.prepend(reasoningBox); pre.textContent=reasoning; }
    $("chars").textContent=Array.from(content).length; $("status").textContent="Streaming…";
    $("chat").scrollTop=$("chat").scrollHeight;
  }
  try {
    const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({messages:history}),signal:controller.signal});
    if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
    const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer="";
    while (true) {
      const {value,done}=await reader.read(); buffer+=decoder.decode(value,{stream:!done});
      const lines=buffer.split("\n"); buffer=lines.pop();
      for (const line of lines) if(line.trim()) receive(JSON.parse(line));
      if(done) { if(buffer.trim()) receive(JSON.parse(buffer)); break; }
    }
    if(!completed) throw new Error("The stream ended before final measurements arrived.");
    $("status").textContent=config.demo ? "Complete · synthetic local demo" : "Complete";
  } catch(error) {
    $("status").textContent=error.name === "AbortError" ? "Stopped · provider billing may continue briefly" : error.message;
  } finally {
    clearInterval(ticker);
    if(metrics) { $("elapsed").textContent=seconds(metrics.elapsed_s); $("first").textContent=seconds(metrics.ttft_s); $("speed").textContent=metrics.end_to_end_tps == null ? "Not reported" : `${metrics.end_to_end_tps.toFixed(1)} t/s`; }
    if(content) history.push({role:"assistant",content}); else history.pop();
    exports.push({prompt,content,reasoning,metrics,complete:completed,synthetic:config.demo});
    controller=null; busy(false); $("prompt").focus();
  }
});
$("stop").onclick=()=>controller?.abort();
$("clear").onclick=()=>{history=[];exports=[];$("chat").replaceChildren();$("status").textContent="New chat";for(const id of ["first","elapsed","speed"])$(id).textContent="—";$("chars").textContent="0";};
$("export").onclick=()=>{const blob=new Blob([JSON.stringify({schema_version:1,model:config?.model,synthetic:config?.demo,turns:exports},null,2)],{type:"application/json"});const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="stream-studio-chat.json";a.click();URL.revokeObjectURL(url);};
fetch("/api/config").then(r=>r.json()).then(value=>{config=value;$("mode").textContent=value.demo?"SYNTHETIC DEMO":"LIVE API";$("model").textContent=`${value.model} · output limit ${value.max_tokens}`;$("status").textContent=value.demo?"Demo ready · no API calls":"Ready · requests consume provider credits";}).catch(()=>{$("status").textContent="Could not load server configuration";$("send").disabled=true;});
