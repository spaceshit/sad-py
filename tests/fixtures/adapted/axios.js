// Adapted from axios README: factory, default override, request precedence.
import axios from "axios";
const instance = axios.create({baseURL: "https://some-domain.com/api/", headers: {"X-Custom-Header": "foobar"}});
instance.defaults.baseURL = "https://api.example.test/v2";
instance.get("/users");
instance.post("/users", {name: "Ada"}, {baseURL: "https://write.example.test"});
