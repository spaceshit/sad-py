import axios from "axios";

const client = axios.create({baseURL: "/api/v2"});
export const listUsers = () => client.get("/users");
export function deleteUser(id: string) {
  return client.delete(`/users/${id}`);
}
export const subscribe = () => new EventSource("/events");
