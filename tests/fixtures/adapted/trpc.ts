// Uses the pinned minimal-react example's createTRPCClient/httpBatchLink setup.
// React query wrappers are replaced with direct proxy calls for this reduced fixture.
import {createTRPCClient, httpBatchLink} from "@trpc/client";
const trpc = createTRPCClient({links:[httpBatchLink({url:"http://localhost:2022"})]});
trpc.greeting.query({name:"Ada"});
trpc.user.create.mutate({name:"Ada"});
