# GroundStation
TJUAV's custom UI ground station

## Available Scripts

In the project directory, you can run:

### `npm i`

Run the above command in client/.
Installs all of the necessary packages (specified in package-lock.json).<br />

### `npm i <package name>`

Run the above command in client/.
Installs the package that you specify (and adds it to package-lock.json).<br />

### `npm start`

Run the above command in client/.
Open [http://localhost:3000](http://localhost:3000) to view it in the browser.

The page will reload if you make edits.<br />
You will also see any lint errors in the console.

### `python main.py`

Open a new terminal and run the above command in server/.
Starts the backend.
If you make any edits, you will need to end the program and restart the backend.

## Code Structure

### Frontend

- Main code
   - _index.js_: Contains the main code for the frontend.
   - _App_.js_: Contains the main code for the frontend.
- Tabs
   - _FlightData.js_: Contains the plane's live telemetry data
   - _FlightPlan.js_: Used to plan out waypoint missons.
   - _Params.js_: Contains a list of parameters that the user can edit and upload to the FC.
   - _Submissions.js_: Displays all submissions that were made to the interop server.
- Components
   - _FlightPlanMap.js_: Contains code for the interactive map used in the FlightPlan tab
   - _FlightPlanToolbar.js_: Contains code for the toolbar used in the FlightPlan tab


### Backend
