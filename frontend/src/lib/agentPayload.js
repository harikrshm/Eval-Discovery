// Decides what to send when creating an agent from the setup form.
//
// An untouched preset is sent by id so the server reuses its stored analysis
// (no LLM call). Any edit to the name, prompt or role label makes it a custom
// agent, which the server has to analyse.
export function buildAgentPayload(preset, { name, prompt, roleLabel }) {
  if (
    preset &&
    name === preset.name &&
    prompt === preset.system_prompt &&
    roleLabel === (preset.user_role_label ?? "")
  ) {
    return { preset_id: preset.id };
  }

  const payload = { name: name.trim(), system_prompt: prompt.trim() };
  const label = roleLabel.trim();
  if (label) payload.user_role_label = label;
  return payload;
}
