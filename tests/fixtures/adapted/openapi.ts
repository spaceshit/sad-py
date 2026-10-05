// Reduced from OpenAPI Generator's BaseAPI request/createFetchParams structure.
// Middleware hooks, response validation, and model types omitted deliberately.
class BaseAPI {
  constructor(configuration) { this.configuration = configuration; }
  async createFetchParams(context) {
    const url = this.configuration.basePath + context.path;
    const init = {method: context.method, headers: context.headers, body: context.body};
    return {url, init};
  }
  async request(context) {
    const {url, init} = await this.createFetchParams(context);
    return fetch(url, init);
  }
}
class PetAPI extends BaseAPI {
  getPet(id) { return this.request({path: `/pets/${id}`, method: "GET"}); }
  addPet(pet) { return this.request({path: "/pets", method: "POST", body: pet}); }
}
const api = new PetAPI({basePath: "https://petstore.example.test/v1"});
api.getPet(12);
api.addPet({name: "Milo"});
