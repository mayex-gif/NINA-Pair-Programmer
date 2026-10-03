import React from 'react'
import RepomapGraph from './RepomapGraph'

function App() {
  return (
    <div style={{ width: '100vw', height: '100vh', margin: 0, padding: 0 }}>
      <RepomapGraph jsonUrl="/.ai_map.json" />
    </div>
  )
}

export default App