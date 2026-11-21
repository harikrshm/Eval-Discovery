// What the user sees when a criteria write fails. An add never had a saved value to keep,
// so only an edit mentions one.
export function criteriaAddError(message) {
  return `Couldn't add the criterion: ${message}`;
}

export function criteriaUpdateError(message) {
  return `Couldn't save your change: ${message}. The last saved value was kept.`;
}
