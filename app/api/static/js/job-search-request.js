/* Send large company selections in the request body so proxy URL limits do not
   prevent the search from reaching Flask. Small searches remain GET requests. */
async function fetchJobSearch(params) {
  const query = params.toString();
  if (query.length <= 6000) {
    return fetch('/api/jobs?' + query);
  }

  const payload = {};
  params.forEach((value, key) => {
    if (key !== 'company') payload[key] = value;
  });
  payload.companies = params.getAll('company');

  return fetch('/api/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
}
