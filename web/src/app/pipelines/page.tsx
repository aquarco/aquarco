import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Paper from '@mui/material/Paper'
import AccountTreeIcon from '@mui/icons-material/AccountTree'

export default function PipelinesPage() {
  return (
    <Box>
      <Typography variant="h5" fontWeight={700} gutterBottom>
        Pipelines
      </Typography>
      <Paper
        variant="outlined"
        sx={{
          p: 6,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 2,
          color: 'text.secondary',
        }}
      >
        <AccountTreeIcon sx={{ fontSize: 64, opacity: 0.3 }} />
        <Typography variant="h6" fontWeight={600}>
          Pipelines — Coming Soon
        </Typography>
        <Typography variant="body2" textAlign="center" maxWidth={480}>
          This page will display all pipeline definitions and their execution history.
          You will be able to inspect individual pipeline runs, see stage-by-stage
          timing, view agent assignments, and track success rates across all task
          categories.
        </Typography>
      </Paper>
    </Box>
  )
}
