import { Chat } from './components/Chat/Chat'

function App() {
  return (
    <div className="min-h-screen bg-gray-100">
      <header className="bg-white shadow-sm">
        <div className="max-w-4xl mx-auto px-4 py-4">
          <h1 className="text-xl font-semibold text-gray-900">Agent System</h1>
        </div>
      </header>
      <main className="max-w-4xl mx-auto px-4 py-6">
        <Chat />
      </main>
    </div>
  )
}

export default App
