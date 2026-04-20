import { resolveBusiness } from "./businesses";
import { ThankYouPage } from "./ThankYouPage";
import { IndexPage } from "./IndexPage";

function App() {
  const business = resolveBusiness(window.location.hostname);
  return business ? <ThankYouPage business={business} /> : <IndexPage />;
}

export default App;
