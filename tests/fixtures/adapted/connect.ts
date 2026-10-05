// Service names verified against pinned eliza_pb.ts RPC declarations.
import {createClient} from "@connectrpc/connect";
import {createConnectTransport} from "@connectrpc/connect-web";
const ElizaService={typeName:"connectrpc.eliza.v1.ElizaService",methods:{say:{name:"Say"},introduce:{name:"Introduce"}}};
const client=createClient(ElizaService,createConnectTransport({baseUrl:"https://demo.connectrpc.com"}));
client.say({sentence:"hello"});
client.introduce({name:"Ada"});
